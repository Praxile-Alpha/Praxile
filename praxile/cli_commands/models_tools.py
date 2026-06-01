from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_models(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    router = ModelRouter(config)
    if getattr(args, "stats", False):
        stats = store.model_routing_stats(limit=args.limit)
        if not stats:
            print("No model routing stats yet.")
            return 0
        print("Model routing stats:")
        for item in stats:
            failures = item.get("failure_patterns") or {}
            failure_text = ", ".join(f"{name}={count}" for name, count in failures.items()) or "none"
            print(
                f"- task_type={item['task_type']} target={item['target']} runs={item['runs']} "
                f"completed={item['completed']} needs_human={item['needs_human']} failed={item['failed']} "
                f"avg_reward={item['average_reward']} avg_latency_ms={item['average_latency_ms']} "
                f"route_failures={item['performance_failures']} patterns={failure_text}"
            )
        return 0
    print("Model roles:")
    if not has_configured_models(config):
        print("- not configured: run `praxile setup` to choose a provider")
    for role_name, role in config.get("model_roles", default={}).items():
        if not isinstance(role, dict):
            continue
        target = f"{role.get('provider')}:{role.get('model')}"
        fallbacks = []
        for item in role.get("fallback", []) or []:
            if isinstance(item, dict):
                fallbacks.append(f"{item.get('provider')}:{item.get('model')}")
            elif isinstance(item, str):
                fallbacks.append(item)
        print(f"- {role_name}: {target}" + (f" fallback={', '.join(fallbacks)}" if fallbacks else ""))
    print("Routes:")
    for key, value in config.get("routing", default={}).items():
        if isinstance(value, str):
            provider_name = value.split(":", 1)[0] if ":" in value else value
            print(f"- {key}: {value} (provider_known={provider_name in router.providers})")
    print("Providers:")
    for name, provider in router.providers.items():
        print(f"- {name}")
        for model in provider.list_models():
            print(
                f"  - {model.name} role={model.role} "
                f"context={model.context_window} tools={model.supports_tools}"
            )
    return 0


from ..patterns import PatternMiner
from ..hypothesis import HypothesisGenerator, CounterexampleChecker
from ..proposals import ProposalComposer

def cmd_tools(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    registry = ToolRegistry(config)
    for tool in registry.describe():
        print(f"- {tool['name']}: {tool['description']}")
    return 0

def cmd_terminal(args: argparse.Namespace, project_root: Path) -> int:
    session = TerminalSession(project_root)
    if args.command:
        keep_going = True
        for command in args.command:
            keep_going, output = session.handle(command)
            if output:
                print(output)
            if not keep_going:
                break
        return 0

    print(session.banner())
    while True:
        try:
            line = safe_input("praxile> ")
        except EOFError:
            print()
            return 0
        keep_going, output = session.handle(line)
        if output:
            print(output)
        if not keep_going:
            return 0

def cmd_channel_list(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    system = ChannelSystem(config)
    bindings = system.list_bindings()
    if not bindings:
        print("No channel bindings yet.")
        return 0
    default_id = config.get("channels", "default")
    for binding in bindings:
        marker = " default" if binding.id == default_id else ""
        print(
            f"{binding.id}{marker}  platform={binding.platform} mode={binding.mode} "
            f"kind={binding.kind} enabled={binding.enabled} token_env={binding.token_env}"
        )
    return 0

def cmd_channel_show(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).get(args.binding_id)
    if not binding:
        print("No channel binding found.")
        return 1
    print(json.dumps(binding.to_dict(), indent=2, ensure_ascii=False))
    return 0

def cmd_channel_bind(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).bind(
        args.platform,
        args.channel_id,
        guild_id=args.guild_id,
        thread_id=args.thread_id,
        name=args.name,
        kind=args.kind,
        mode=args.mode,
        token_env=args.token_env,
        require_mention=args.require_mention,
        allow_free_response=args.free_response,
        auto_thread=args.auto_thread,
        skill=args.skill,
        prompt=args.prompt,
        project_scope=args.project_scope,
        make_default=args.default,
    )
    print(f"Bound {binding.id}")
    print(f"Config: {config.paths.config}")
    print(f"Token source: ${binding.token_env}")
    return 0

def cmd_channel_unbind(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).unbind(args.binding_id)
    print(f"Unbound {binding.id}")
    return 0

def cmd_channel_env(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    overlay = ChannelSystem(config).as_env_overlay()
    if not overlay:
        print("No enabled channel overlay values.")
        return 0
    for key in sorted(overlay):
        print(f"{key}={overlay[key]}")
    return 0

def cmd_gateway_serve(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    server = serve_gateway(config.paths.root, host=args.host, port=args.port, token=args.token)
    print(f"Praxile gateway serving http://{args.host}:{args.port}")
    print(f"Console: http://{args.host}:{args.port}/")
    print(f"Project: {config.paths.root}")
    if args.token:
        print("Auth: token required")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0

__all__ = [name for name in globals() if name.startswith("cmd_")]
