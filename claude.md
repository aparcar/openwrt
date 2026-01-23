# Claude Development Notes

Guidelines for working on this codebase.

## Workflow

- **Always commit after successful testing** - When tests pass or a feature works correctly, commit immediately before moving on to the next task.

## Container Build System

Build containers with content-based caching:
```bash
python3 -m owrt container build              # Build base + tools
python3 -m owrt container build -t x86/64    # Build toolchain for target
python3 -m owrt container status             # Check image status
python3 -m owrt container hash --json        # Get content hashes for CI
```

## Host Tools

13 host tools are built in the tools container:
- **Core bootstrap**: libdeflate, patch, tar, zstd
- **Compression**: xz, zlib  
- **Build tools**: apk, lua, mkimage, squashfs4, mtd-utils, fwtool, arm-trusted-firmware-tools

Add new tools in `owrt/tools/<name>/tool.yaml`.

## Testing

Run unit tests:
```bash
python3 -m pytest owrt/tests/ -v
```

Test container build:
```bash
python3 -m owrt container build
docker run --rm owrt-tools:latest apk --version
```
