# Go Example

```bash
praxile init --force
praxile doctor
praxile run "Fix the greeting test" --test-command "go test ./..."
praxile review --interactive
```

Expected detection:

- stacks: `go`
- markers: `go.mod`
- commands: `go test ./...`
