# Build Sandboxing in OpenWrt

## Overview

OpenWrt now includes build sandboxing to provide isolation during package compilation and installation. This security feature helps prevent:

- Unauthorized modifications to the developer's machine during package builds
- Packages pulling in wrong dependencies from the host system
- Unintended filesystem access during the build process

## How It Works

The build sandbox is implemented through the `scripts/build-sandbox.sh` wrapper script that is automatically invoked during:
- `Build/Compile` steps in package builds
- `Build/Install` steps in package builds  
- `Host/Compile` steps in host tool builds

### Isolation Methods

The sandbox uses different isolation methods depending on the platform and available capabilities:

#### Linux with Namespace Support
On Linux systems with namespace support, the sandbox uses `unshare` to create isolated namespaces:
- Mount namespace: Isolates filesystem mount points
- UTS namespace: Isolates hostname
- IPC namespace: Isolates System V IPC
- PID namespace: Isolates process IDs (when available)

#### Linux without Namespace Support / macOS
When namespace isolation is not available, the sandbox falls back to environment isolation:
- Cleans and resets environment variables
- Preserves only essential variables needed for builds
- Provides basic protection against environment pollution

## Configuration

### Enabling/Disabling Sandbox

The build sandbox is **enabled by default**. You can control it through the build configuration:

1. Run `make menuconfig`
2. Navigate to: `Global build settings` → `General build options`
3. Toggle `Enable build sandboxing for package compilation`

Alternatively, you can set it directly in `.config`:
```
CONFIG_BUILD_SANDBOX=y  # Enable (default)
CONFIG_BUILD_SANDBOX=n  # Disable
```

### Environment Variables

- `OPENWRT_SANDBOX_METHOD`: Override sandbox method
  - `auto`: Automatic selection (default)
  - `env`: Force environment isolation only
  - `none`: Disable sandbox (not recommended)

Example:
```bash
export OPENWRT_SANDBOX_METHOD=env
make package/example/compile
```

## Platform Support

| Platform | Primary Method | Fallback |
|----------|---------------|----------|
| Linux | Namespace isolation | Environment isolation |
| macOS | Environment isolation | N/A |
| Other | Environment isolation | N/A |

## Troubleshooting

### Build Fails in Sandbox

If a package fails to build with the sandbox enabled:

1. Check the build log for sandbox-related warnings
2. Try disabling sandbox for that specific package (see below)
3. Report the issue if it's a legitimate sandbox bug

### Disabling Sandbox for Specific Packages

If a package has issues with the sandbox, you can disable it globally:

```bash
make menuconfig
# Disable "Enable build sandboxing for package compilation"
```

Or set it in your environment for a single build:
```bash
CONFIG_BUILD_SANDBOX=n make package/problematic-package/compile
```

### Debugging

To see which isolation method is being used, look for warnings in the build output:
- `Warning: namespace isolation failed, using environment isolation` - Fallback to env isolation
- `Warning: unshare not available, using environment isolation` - No namespace support

## Security Considerations

While the build sandbox provides useful isolation, it is not a complete security solution:

- The sandbox is designed to prevent **accidental** issues, not malicious attacks
- Sandboxed processes still run with your user privileges
- File system access to build directories is necessary and allowed
- Network access is not restricted

For maximum security when building untrusted packages:
- Use a dedicated build VM or container
- Review package Makefiles before building
- Use the sandbox as an additional layer of defense

## Implementation Details

The sandbox is implemented in:
- `scripts/build-sandbox.sh`: Main sandbox wrapper script
- `rules.mk`: BUILD_SANDBOX variable definition
- `include/package-defaults.mk`: Integration into Build/Compile and Build/Install
- `include/host-build.mk`: Integration into Host/Compile
- `config/Config-build.in`: Configuration option

## Future Improvements

Potential enhancements for future versions:
- Network namespace isolation (restrict network access during builds)
- Read-only bind mounts for sensitive directories
- Resource limits (CPU, memory)
- Seccomp filters for syscall restriction
- Enhanced macOS support using sandbox-exec
