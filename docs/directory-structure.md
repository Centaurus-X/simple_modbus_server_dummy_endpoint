# Directory structure

```text
modbus_sim_server/
├─ .github/
│  └─ workflows/
│     └─ tests.yml
├─ config/
│  └─ device_state_data.json
├─ docs/
│  ├─ components.md
│  └─ directory-structure.md
├─ logs/
│  ├─ .gitkeep
│  └─ sensor_data_tmp/
│     └─ .gitkeep
├─ tests/
│  ├─ __init__.py
│  └─ test_runtime_history_sync.py
├─ web_interface/
│  ├─ app.js
│  ├─ index.html
│  └─ style.css
├─ CHANGELOG.md
├─ CONTRIBUTING.md
├─ LICENSE
├─ README.md
├─ SECURITY.md
├─ modbus_sim_server.py
├─ pyproject.toml
└─ requirements.txt
```

Generated files such as logs, bytecode caches and runtime sensor history are ignored by Git.
