# Harbor 独立容器租约

`praxile.eval.harbor_lease.HarborDockerLease` 提供显式的
prepare/reset/execute/collect/cancel/cleanup 生命周期。它创建自己的 Harbor
Docker environment，不接管 `Trial` 传给 `PraxileAgent` 的环境，不自动替换旧 runner。

## 使用边界

- 可选运行环境固定 Harbor 0.18.0 或 0.22.0；不加入基础安装依赖。
- 镜像必须以 `@sha256:` 固定，内含 Linux/POSIX Python 3 和 shell。
- 可选 `inputs: tuple[InputArtifact, ...]` 与 `input_source_root` 必须同时提供。
  `source_ref` 在此接口仅接受来源根目录下的相对文件路径，不下载 URL。
  显式清单中的文件逐一校验 SHA-256、拒绝软/硬链接，复制为独立快照后以只读
  bind mount 挂到 `/workspace/inputs/<InputArtifact.path>`；来源目录及未声明文件不挂载。
  每文件最多 32 MiB、总计 128 MiB、最多 256 项；拒绝重复和祖先路径冲突。
  reset 重新校验快照的内容、根身份及文件集合，不重新读取可变来源文件。
  输入公开性仍由调用方负责分类；不能自动识别被错误声明为公开输入的 rubric。
- `lease.public_inputs` 返回新建的公开视图，仅含 `inputs/...` 相对路径和摘要，
  不含宿主 source_ref。Praxile Native 的文件工具以 `/workspace` 为根；早期的
  `/inputs` 挂载位于根外，不能通过路径安全检查，因此已迁入工作区。执行器应使用
  公开视图提供的路径，不拼接宿主路径；来源清单的路径不直接等于工具路径。
- 每代环境创建随机 Compose project/session；reset 删除自己的旧环境后创建新代。
- 可选 persist_workspace=True 将该代独立 `SESSION/workspace/` bind 到 /workspace；
  默认 False 保持原容器内工作区。每次 reset 创建新目录，不自动恢复旧内容。
  旧代工作区保留，cleanup/recovery 不删除宿主文件；公开输入仍来自独立只读快照。
- 固定工作目录 `/workspace`；执行 argv 经 shell quoting 传入 Harbor。
- 超时或取消会销毁整个租约容器，而不是尝试保留不确定的命令状态。继续工作需 reset。
- 取消 prepare/reset 会先等待所属操作结束，再删除环境；重复取消不能遗失所有权。
- cleanup 调用 Harbor stop 后查询自己 session 标签下的 container/network/volume。
  只有全部为空才记为 closed；daemon 不可用、查询失败或资源残留都报错。
- collect 在容器内复用 descriptor-relative 读取：拒绝链接和特殊文件、检查根目录
  身份和读取期间变化、限制字节数，以 base64 传输。它不是原子文件系统快照。
- 生命周期保留本地 trial 日志，不自动删除宿主目录；没有加载或传输模型凭据。
- 创建容器前写入 ownership.json，记录租约身份、owner PID、固定镜像和每代
  session 的 Docker daemon ID。reset 不丢弃前代记录；只有独立查询确认资源
  消失后才记录 cleanup_verified。daemon 改变时拒绝在另一个 daemon 上清理。
- 新租约在生命周期内持有 owner.lock 的非阻塞 POSIX flock，正常 cleanup 后
  才释放；进程退出由内核释放。记录锁文件 device/inode，防止用替换的锁冒充
  原锁。只支持本机 POSIX 文件系统的协作式排他，不依赖 PID 是否重用来判定。

```python
from pathlib import Path
from praxile.eval.harbor_lease import HarborDockerLease

# 在调用方的 async 函数内；IMAGE_DIGEST 来自已验证的镜像摘要。
async with HarborDockerLease(Path(".praxile/leases"), image=IMAGE_DIGEST) as lease:
    result = await lease.execute(["python3", "-c", "print('ready')"], timeout=10)
    assert result.returncode == 0
    await lease.reset()
```

## 验证与未完成项

已有 ownership.json 的租约可用下列只读命令检查（不要求安装 Harbor）：

```bash
python -m praxile.eval.lease_inspect --lease-dir /absolute/path/to/harbor-lease-ID
```

命令先验证记录格式和 session 范围，再查询相同 daemon 下该 session 的容器、
网络和卷；不调用 stop/rm，不修改记录，也不重新启动模型。退出码 0 表示这次
观测各代资源均不存在；1 表示有资源或 daemon 不匹配；2 表示检查失败、状态未知。
历史 cleanup_verified 不能替代当前查询；owner PID 只是记录，不能证明进程仍活着
或资源已经成为孤儿。不得仅凭本结果推导可以删除资源。旧无 ownership.json 的
租约不猜测 session，也不自动迁移。记录不是签名认证，不防同权限宿主篡改。

### 显式回收

**回收会删除容器和卷，未采集的成果可能丢失。先检查、保存所需成果，再明确确认。**
从只读检查结果中取得全部 session，按原顺序传入：

```bash
python -m praxile.eval.lease_recover \
  --lease-dir /absolute/path/to/harbor-lease-ID \
  --cleanup --discard-uncollected \
  --confirm-session praxile-lease-SESSION_ID
```

只接受有锁身份的新记录。活跃 owner 或另一个 recovery 持锁时拒绝；取得锁后
重读记录并核对 inode、daemon ID，逐项 inspect 标签后才删除对应容器、网络、卷。
不删除镜像、宿主输入或产物，不使用 prune，不触碰其他 session。每次操作形成
独立 recovery-UUID.json，保留逐项删除和失败信息，原 ownership.json 不改写。
完成后再次实时检查全部资源；重复清理已空环境为无删除操作的新审计记录。

回收过程中取消会先等已授权清理和审计收尾，期间继续持锁。它不是自动 janitor，
不证明原 PID 已死亡，也不是 checkpoint 恢复；只证明没有遵守该协议的活跃 owner
持有锁。旧无锁记录、锁替换、daemon 变化或标签不符均拒绝。NFS/跨宿主锁语义、
恶意宿主并发篡改及不遵守协议的外部程序不在已验证范围。

资源测试为 `tests/resource/test_harbor_lease.py`。默认不创建 Docker 环境；真实
测试需显式设置 `PRAXILE_TEST_LEASE_IMAGE`，使用固定版本环境和 `--forked`。
测试覆盖创建、执行、受限采集、reset、超时、取消和释放后的资源查询；fixture
覆盖 Harbor 吞掉清理错误、重复取消和清理重试。
额外真实容器测试传入当前源码包，调用 FileSystemEnv.read_file/list_dir/write_file，
验证输入可见、越界读取被阻止以及输入覆盖/新增触发只读文件系统错误。此验证不
调用模型，不等同于完整 AgentRuntime 或正式 benchmark 验收。

独立故障测试 `tests/resource/test_harbor_lease_crash.py` 在固定 Harbor 环境中：

1. 创建独立子进程并等待租约 prepare 完成的明确握手。
2. 向该子进程自己的进程组发送 SIGKILL，等待并确认退出码为 -SIGKILL。
3. 用新的 Python 进程运行 lease_inspect，确认能发现真实遗留容器，且不修改 ownership.json。
4. 验证 owner 活跃时正式回收 API 拒绝；强杀后新进程运行显式回收 CLI，审计删除并确认全部不存在。

Harbor 0.18.0 与 0.22.0 均已在 Mac ARM64 Docker Desktop 验证；测试为 slow +
integration + resource，必须显式设置固定 PRAXILE_TEST_LEASE_IMAGE，并使用
--forked。无真实模型调用。正式回收路径用 owner lock、明确 session 确认、daemon
和标签检查保护，不沿用此前仅由测试夹具证明 owner 已退出的前提。
该证据证明强杀后记录可用、独立检查可发现遗留资源，不证明动作 checkpoint
恢复、断电恢复或无人工确认的生产自动回收已经完成。

持久化模式另验证了 reset 后旧代文件仍保留、新代不可见旧文件；Native runner
生成的 answer.txt 在独立回收目录和保留工作区都存在；SIGKILL 后正式 recovery
删除容器时宿主 checkpoint 样本不被删除。故障样本是测试文件，不是可恢复的
真实 AgentRuntime checkpoint。可见工作区是实时挂载，不是多文件原子快照；
不能在活动写入期间直接把它当作一致的恢复点。保留的 .praxile 状态和模型产物
均应视为不可信输入，尚未开放自动续跑或自动加载经验。

本后端不是敌对代码安全沙箱：只读输入已在显式清单路径实现，但尚未建立完整
下载/私有评测存储隔离、网络隔离策略或宿主资源配额。宿主同权限进程仍可能在
校验后修改快照；只读挂载不等于对恶意宿主的防护。Harbor 启动/停止自身没有整体硬期限；Docker daemon
失联、SIGKILL、断电后的跨进程恢复尚未验收。Python 执行输出仍由 Harbor 缓冲，
不能把 collect 的字节上限等同于所有命令的输出上限。正式 runner 接入和完整
benchmark 验收仍待完成，不据此勾选路线图的整个环境协议项。
