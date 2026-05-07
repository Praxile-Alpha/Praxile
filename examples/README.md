# Praxile Examples

These small repositories show how the same Environment -> Reward -> Experience loop applies across stacks.

Try any example from the repository root:

```bash
cd examples/go
praxile init --force
praxile doctor
praxile run "Fix the greeting test" --test-command "go test ./..."
```

Each example is intentionally tiny so contributors can inspect the resulting `.praxile/` trajectory, reward report, proposal files, and accepted memory/skill updates without unrelated framework noise.

## Included

- `react/`: UI-sensitive frontend project with Node test/build scripts.
- `go/`: Go module with `go test ./...`.
- `rust/`: Rust crate with `cargo test`.
