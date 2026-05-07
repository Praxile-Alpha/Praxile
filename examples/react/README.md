# React Example

This example demonstrates a UI-sensitive project where Praxile should detect a Node/React/Vite stack, seed verification commands, and still require human acceptance for visual/interaction quality.

```bash
npm install
praxile init --force
praxile doctor
praxile run "Improve the selected state feedback" --test-command "npm test" --test-command "npm run build"
praxile review --interactive
```

Expected detection:

- stacks: `node`, `react`, `vite`
- package manager: `npm` unless a lockfile indicates otherwise
- commands: `npm test`, `npm run build`
