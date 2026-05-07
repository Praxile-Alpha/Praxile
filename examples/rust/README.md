# Rust Example

```bash
praxile init --force
praxile doctor
praxile run "Fix the formatter test" --test-command "cargo test"
praxile review --interactive
```

Expected detection:

- stacks: `rust`
- markers: `Cargo.toml`
- commands: `cargo test`
