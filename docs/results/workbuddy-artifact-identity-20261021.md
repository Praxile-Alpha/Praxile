# Office 产物身份离线复核

本次复用 [真实留存基线](workbuddy-retention-baseline-20261021.md)，没有启动
执行器、Docker 或模型请求，不覆盖原始实验收据。

通过正式 WorkBuddy importer 重新核对 trial 身份、原生结果摘要、事件投影与
留存文件；再比较原生结果中的 artifact_inventory 和宿主回收清单。

| 字段 | 值 |
| --- | --- |
| task | drug-inventory-split-L2-006 |
| source_result_sha256 | 3034baa60a28a01d88a846cd850713a265aa047a366ca49a3607145ee4996954 |
| artifact | result.xlsx |
| size | 27091 |
| sha256 | b82a32539987bd2e3c3c00ea2094b7611078bbb8a8b19b259ca97240bda074d2 |
| identity status | matched |
| observed / retained | 1 / 1 |
| added / missing / changed | 0 / 0 / 0 |

新增字段为 `native_trace_binding.artifact_identity`，版本
`praxile.artifact_identity.v1`。它只比较字节身份；文件格式不支持解析但有完整
摘要时仍可比较。旧结果没有 inventory 或清单不完整时返回 unknown，不能由
旁边同名文件推断匹配。未绑定原生结果的旧 trial 仍为 unbound。

`provenance_attested`、`verifier_input_verified`、`content_verified` 全部为 false。
匹配不能证明评分器实际消费的是这些字节，也不能排除同一容器中恶意执行器
伪造声明或文件先改后恢复。此功能不改变官方分数，不赋予晋升资格。

定向测试覆盖两个 importer 的一致、同大小不同内容、大小不符、缺文件、额外
文件、重复条目、路径逃逸、缺摘要、目录不符、缺清单和未完成清单，以及原有
trial/trace 摘要和事件投影校验。公开报告不包含文档正文、私有 rubric 或密钥。
