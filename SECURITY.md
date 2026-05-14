# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 2.3.x | Yes |
| Earlier versions | No |

## Reporting a vulnerability

Please report security issues privately to the repository maintainer. Include:

- affected version or commit,
- operating system and Python version,
- reproduction steps,
- expected and observed behavior,
- potential impact.

Do not publish exploit details before a fix or mitigation is available.

## Operational security notes

- The default server binds to `0.0.0.0` for local lab convenience. Restrict the host, firewall the ports or run behind a trusted network boundary for production-like environments.
- The web dashboard has no built-in authentication.
- Runtime logs and sensor history may contain operational data. Treat `logs/` as sensitive when running real integration scenarios.
- Do not expose the Modbus or WebSocket ports directly to untrusted networks.
