/**
 * ============================================================================
 * ADVANCED MODBUS SIMULATION SERVER v2.3 - WEB APPLICATION
 * ============================================================================
 * Real-time web interface for the Modbus Simulation Server
 * Features: WebSocket communication, Live-Charts, Device-Management
 *
 * v2.3 FIXES:
 * - Complete synchronization of visible server configuration
 * - Batch updates include device metadata for UI consistency
 * - batch_value_update handler for efficient value updates
 * - Correct state synchronization for all update types
 * - Throttled UI updates prevent performance issues
 * - Actuator values are rendered correctly
 * ============================================================================
 */

// ============================================================================
// APPLICATION STATE
// ============================================================================

var App = {
    // State
    state: {
        sensors: {},
        actuators: {},
        config: {},
        simulationModes: {},
        history: {},
        connected: false,
        theme: 'dark',
        serverMeta: {},
        selectedDevice: null,
        lastStats: null,
        logs: [],
    },

    // WebSocket
    ws: null,
    reconnectAttempts: 0,
    maxReconnectAttempts: 10,

    // Charts
    charts: {},

    // UI update throttling
    _uiUpdatePending: false,
    _uiUpdateTimer: null,
    _gridUpdatePending: false,
    _gridUpdateTimer: null,

    // ========================================================================
    // INITIALIZATION
    // ========================================================================

    init: function() {
        console.log('Initializing Modbus Simulation Server UI v2.3...');

        this.initTabs();
        this.initCharts();
        this.initEventListeners();
        this.connectWebSocket();
        this.startStatsPolling();

        // Load theme from localStorage
        var savedTheme = localStorage.getItem('modbus_theme');
        if (savedTheme) {
            this.setTheme(savedTheme);
        }

        console.log('UI initialized');
    },

    // ========================================================================
    // WEBSOCKET CONNECTION
    // ========================================================================

    connectWebSocket: function() {
        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.host + '/ws';

        console.log('Connecting to WebSocket:', wsUrl);

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = this.handleWsOpen.bind(this);
        this.ws.onclose = this.handleWsClose.bind(this);
        this.ws.onerror = this.handleWsError.bind(this);
        this.ws.onmessage = this.handleWsMessage.bind(this);
    },

    handleWsOpen: function() {
        console.log('WebSocket connected');
        this.state.connected = true;
        this.reconnectAttempts = 0;
        this.updateConnectionStatus(true);
        this.showToast('Connection established', 'success');
        this.addLog('INFO', 'WebSocket connected');
    },

    handleWsClose: function() {
        console.log('WebSocket disconnected');
        this.state.connected = false;
        this.updateConnectionStatus(false);
        this.showToast('Connection lost', 'error');
        this.addLog('ERROR', 'WebSocket disconnected');

        // Reconnect with backoff
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            var delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            this.reconnectAttempts++;
            console.log('Reconnecting in ' + delay + 'ms (attempt ' + this.reconnectAttempts + ')');
            setTimeout(this.connectWebSocket.bind(this), delay);
        }
    },

    handleWsError: function(error) {
        console.error('WebSocket error:', error);
        this.addLog('ERROR', 'WebSocket error occurred');
    },

    handleWsMessage: function(event) {
        try {
            var data = JSON.parse(event.data);
            this.processMessage(data);
        } catch (e) {
            console.error('JSON Parse error:', e);
        }
    },

    processMessage: function(data) {
        switch(data.type) {
            case 'initial_state':
                this.handleInitialState(data);
                break;
            case 'registry_update':
                this.handleRegistryUpdate(data);
                break;
            case 'value_update':
                this.handleValueUpdate(data);
                break;
            case 'batch_value_update':
                // Batch updates from the server
                this.handleBatchValueUpdate(data);
                break;
            case 'server_stats':
                this.handleServerStats(data);
                break;
            case 'config_update':
                this.handleConfigUpdate(data);
                break;
            case 'theme_changed':
                this.setTheme(data.theme);
                break;
            case 'history_data':
                this.handleHistoryData(data);
                break;
            case 'config_changed':
                this.handleConfigChanged(data);
                break;
        }
    },

    sendMessage: function(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            return true;
        }
        console.warn('WebSocket not connected');
        return false;
    },

    // ========================================================================
    // MESSAGE HANDLERS
    // ========================================================================

    handleInitialState: function(data) {
        console.log('Received initial state');
        this.state.sensors = data.sensors || {};
        this.state.actuators = data.actuators || {};
        this.state.config = this.normalizeServerConfig(data.config || {});
        this.state.simulationModes = data.simulation_modes || {};
        this.state.history = this.normalizeHistoryMap(data.history || {});
        this.state.serverMeta = data.server_meta || {};

        if (data.theme) {
            this.setTheme(data.theme);
        }

        this.syncServerConfigUI();

        this.updateAllUI();
        this.addLog('SUCCESS', 'Initial data received');
    },

    handleRegistryUpdate: function(data) {
        var event = data.event;
        var deviceData = data.data;

        if (event === 'sensor_added' || event === 'sensor_updated') {
            this.state.sensors[deviceData.id] = deviceData;
            this.addLog('SUCCESS', 'Sensor registered: ' + deviceData.id);
        } else if (event === 'actuator_added' || event === 'actuator_updated') {
            this.state.actuators[deviceData.id] = deviceData;
            this.addLog('SUCCESS', 'Actuator registered: ' + deviceData.id);
        } else if (event === 'device_reset') {
            var resetDeviceId = deviceData.device_id || '';
            var resetDeviceType = deviceData.device_type || '';
            var resetDevice = deviceData.device || null;

            if (resetDeviceId && this.state.history[resetDeviceId]) {
                delete this.state.history[resetDeviceId];
            }

            if (resetDeviceType === 'sensor' && resetDevice) {
                this.state.sensors[resetDeviceId] = resetDevice;
            } else if (resetDeviceType === 'actuator' && resetDevice) {
                this.state.actuators[resetDeviceId] = resetDevice;
            } else {
                if (this.state.sensors[resetDeviceId]) {
                    this.state.sensors[resetDeviceId].value = 0;
                    this.state.sensors[resetDeviceId].read_count = 0;
                    this.state.sensors[resetDeviceId].last_read = null;
                    this.state.sensors[resetDeviceId].simulation_mode = 'random';
                }
                if (this.state.actuators[resetDeviceId]) {
                    this.state.actuators[resetDeviceId].value = 0;
                    this.state.actuators[resetDeviceId].write_count = 0;
                    this.state.actuators[resetDeviceId].last_write = null;
                }
            }

            this.updateCharts();
            this.addLog('WARNING', 'Device reset: ' + resetDeviceId);
        }

        this.scheduleGridUpdate();
    },

    handleValueUpdate: function(data) {
        /**
         * Legacy single-value update for backward compatibility.
         */
        var deviceId = data.device_id;
        var value = data.value;

        if (data.device_type === 'sensor') {
            if (data.device) {
                this.state.sensors[deviceId] = Object.assign({}, this.state.sensors[deviceId] || {}, data.device);
                this.scheduleGridUpdate();
            } else if (this.state.sensors[deviceId]) {
                this.state.sensors[deviceId].value = value;
            }
        } else if (data.device_type === 'actuator') {
            if (data.device) {
                this.state.actuators[deviceId] = Object.assign({}, this.state.actuators[deviceId] || {}, data.device);
                this.scheduleGridUpdate();
            } else if (this.state.actuators[deviceId]) {
                this.state.actuators[deviceId].value = value;
            }
        }

        this.updateDeviceValue(deviceId, value);
        this.addChartDatapoint(deviceId, value, data.timestamp_ms || null);
        this.scheduleChartUpdate();
    },

    handleBatchValueUpdate: function(data) {
        /**
         * Efficient batch update.
         * Receives all changed values in a single packet.
         */
        var deviceType = data.device_type;
        var values = data.values || {};
        var devices = data.devices || {};
        var history = data.history || {};
        var stateMap = (deviceType === 'sensor')
            ? this.state.sensors
            : this.state.actuators;

        var deviceIds = Object.keys(values);
        var self = this;
        var requireGridSync = false;

        deviceIds.forEach(function(deviceId) {
            var value = values[deviceId];
            var samples = history[deviceId] || [];
            var deviceSnapshot = devices[deviceId] || null;

            if (deviceSnapshot) {
                stateMap[deviceId] = Object.assign({}, stateMap[deviceId] || {}, deviceSnapshot);
                requireGridSync = true;
            } else {
                if (!stateMap[deviceId]) {
                    stateMap[deviceId] = { id: deviceId, value: value };
                    requireGridSync = true;
                } else {
                    stateMap[deviceId].value = value;
                }
            }

            self.updateDeviceValue(deviceId, value);

            if (samples.length > 0) {
                self.appendHistorySamples(deviceId, samples);
            } else {
                self.addChartDatapoint(deviceId, value, data.timestamp_ms || null);
            }
        });

        if (requireGridSync) {
            this.scheduleGridUpdate();
        }

        this.scheduleChartUpdate();
    },

    handleServerStats: function(data) {
        this.state.lastStats = data;

        this.updateElement('sensorCount', data.total_sensors || 0);
        this.updateElement('actuatorCount', data.total_actuators || 0);
        this.updateElement('totalReads', this.formatNumber(data.total_reads || 0));
        this.updateElement('totalWrites', this.formatNumber(data.total_writes || 0));
        this.updateElement('wsCount', data.connected_websockets || 0);
        this.updateElement('uptime', this.formatUptime(data.uptime_seconds || 0));
        this.updateElement('errorCount', data.errors || 0);

        // Datenpunkte zaehlen
        var totalDataPoints = 0;
        Object.values(this.state.history).forEach(function(h) {
            totalDataPoints += h.length;
        });
        this.updateElement('dataPointsCount', totalDataPoints);
    },

    handleConfigUpdate: function(data) {
        var deviceId = data.device_id;
        var config = data.config;

        // FIX: Config auch im lokalen State aktualisieren
        if (this.state.sensors[deviceId]) {
            this.state.sensors[deviceId].simulation_mode = config.mode || 'random';
            this.state.sensors[deviceId].config = config;
        }
        if (this.state.actuators[deviceId]) {
            this.state.actuators[deviceId].simulation_mode = config.mode || 'random';
            this.state.actuators[deviceId].config = config;
        }

        this.addLog('INFO', 'Configuration updated: ' + deviceId);
        this.scheduleGridUpdate();
    },

    handleHistoryData: function(data) {
        this.state.history[data.device_id] = this.normalizeHistorySeries(data.data || []);
        this.trimStoredHistory(data.device_id);
        this.updateCharts();
    },

    handleConfigChanged: function(data) {
        /**
         * Server changed CONFIG values, for example intervals.
         * Lokalen State und UI-Inputs synchronisieren.
         */
        if (data.config) {
            this.state.config = this.normalizeServerConfig(data.config);
            this.state.serverMeta = data.server_meta || this.state.serverMeta || {};
            this.syncServerConfigUI();
            this.addLog('INFO', 'Server configuration updated');
        }
    },

    normalizeHistoryPoint: function(point) {
        if (!point) {
            return null;
        }

        var value = (point.value !== undefined) ? point.value : point.y;
        var timestampMs = null;

        if (point.timestamp_ms !== undefined && point.timestamp_ms !== null) {
            timestampMs = Number(point.timestamp_ms);
        } else if (point.timestamp !== undefined && point.timestamp !== null) {
            timestampMs = Number(point.timestamp);
            if (timestampMs < 1000000000000) {
                timestampMs = timestampMs * 1000;
            }
        } else if (point.x !== undefined && point.x !== null) {
            timestampMs = Number(point.x);
            if (timestampMs < 1000000000000) {
                timestampMs = timestampMs * 1000;
            }
        }

        if (!isFinite(timestampMs)) {
            timestampMs = Date.now();
        }

        return { x: Math.round(timestampMs), y: value };
    },

    normalizeHistorySeries: function(series) {
        var normalized = (series || [])
            .map(this.normalizeHistoryPoint.bind(this))
            .filter(function(point) {
                return point !== null && point.y !== undefined;
            });

        normalized.sort(function(a, b) {
            return a.x - b.x;
        });

        return normalized;
    },

    normalizeHistoryMap: function(historyMap) {
        var normalized = {};
        var self = this;

        Object.keys(historyMap || {}).forEach(function(deviceId) {
            normalized[deviceId] = self.normalizeHistorySeries(historyMap[deviceId]);
        });

        return normalized;
    },

    getHistoryStorageLimit: function() {
        var simConfig = (this.state.config && this.state.config.simulation) || {};
        return parseInt(simConfig.history_length, 10) || 500;
    },

    normalizeServerConfig: function(config) {
        var normalized = config || {};

        if (!normalized.modbus) normalized.modbus = {};
        if (!normalized.webserver) normalized.webserver = {};
        if (!normalized.simulation) normalized.simulation = {};
        if (!normalized.pid) normalized.pid = {};
        if (!normalized.logging) normalized.logging = {};

        normalized.modbus.port = parseInt(normalized.modbus.port, 10) || 5020;
        normalized.modbus.sensor_threshold = parseInt(normalized.modbus.sensor_threshold, 10) || 500;
        normalized.webserver.port = parseInt(normalized.webserver.port, 10) || 8080;
        normalized.simulation.update_interval_ms = parseInt(normalized.simulation.update_interval_ms, 10) || 4000;
        normalized.simulation.broadcast_interval_ms = parseInt(normalized.simulation.broadcast_interval_ms, 10) || 5000;
        normalized.simulation.history_length = parseInt(normalized.simulation.history_length, 10) || 500;

        return normalized;
    },

    getServerVersion: function() {
        var version = this.state.serverMeta && this.state.serverMeta.app_version;
        return version || '2.3';
    },

    getChartMaxPoints: function() {
        var chartHistoryEl = document.getElementById('chartHistory');
        return chartHistoryEl ? (parseInt(chartHistoryEl.value, 10) || 100) : 100;
    },

    trimStoredHistory: function(deviceId) {
        if (!this.state.history[deviceId]) {
            return;
        }

        var maxPoints = this.getHistoryStorageLimit();
        while (this.state.history[deviceId].length > maxPoints) {
            this.state.history[deviceId].shift();
        }
    },

    appendHistorySamples: function(deviceId, samples) {
        if (!this.state.history[deviceId]) {
            this.state.history[deviceId] = [];
        }

        var normalized = this.normalizeHistorySeries(samples);
        Array.prototype.push.apply(this.state.history[deviceId], normalized);
        this.state.history[deviceId].sort(function(a, b) {
            return a.x - b.x;
        });
        this.trimStoredHistory(deviceId);
    },

    getChartSeries: function(deviceId) {
        var series = this.state.history[deviceId] || [];
        var maxPoints = this.getChartMaxPoints();

        if (series.length <= maxPoints) {
            return series;
        }

        return series.slice(series.length - maxPoints);
    },

    formatChartTimestamp: function(timestampMs, withDate) {
        var date = new Date(Number(timestampMs));
        if (withDate) {
            return date.toLocaleString('en-US');
        }
        return date.toLocaleTimeString('en-US');
    },

    syncIntervalInputs: function() {
        /**
         * Synchronisiert die Intervall-Inputs mit den aktuellen Config-Werten.
         */
        var simConfig = (this.state.config && this.state.config.simulation) || {};
        var updateValue = parseInt(simConfig.update_interval_ms, 10) || 4000;
        var broadcastValue = parseInt(simConfig.broadcast_interval_ms, 10) || 5000;

        var updateEl = document.getElementById('simUpdateInterval');
        if (updateEl) {
            updateEl.value = updateValue;
        }

        var broadcastEl = document.getElementById('simBroadcastInterval');
        if (broadcastEl) {
            broadcastEl.value = broadcastValue;
        }
    },

    syncServerConfigUI: function() {
        var config = this.normalizeServerConfig(this.state.config || {});
        var modbusConfig = config.modbus || {};
        var webConfig = config.webserver || {};

        this.state.config = config;
        this.syncIntervalInputs();

        this.updateElement('cfgModbusPort', modbusConfig.port || 5020);
        this.updateElement('cfgWebPort', webConfig.port || 8080);
        this.updateElement('cfgThreshold', modbusConfig.sensor_threshold || 500);
        this.updateElement('modbusPort', modbusConfig.port || 5020);
        this.updateElement('appVersionBadge', 'v' + this.getServerVersion());
        this.updateElement('aboutVersion', this.getServerVersion());
        this.updateElement('aboutPymodbusVersion', (this.state.serverMeta && this.state.serverMeta.pymodbus_version) || '-');
    },

    applySimIntervals: function() {
        /**
         * Sendet neue Intervall-Werte an den Server.
         */
        var updateEl = document.getElementById('simUpdateInterval');
        var broadcastEl = document.getElementById('simBroadcastInterval');

        var updateMs = updateEl ? parseInt(updateEl.value) : null;
        var broadcastMs = broadcastEl ? parseInt(broadcastEl.value) : null;

        if (updateMs && (updateMs < 50 || updateMs > 60000)) {
            this.showToast('Simulations-Intervall: 50-60000ms', 'warning');
            return;
        }
        if (broadcastMs && (broadcastMs < 50 || broadcastMs > 60000)) {
            this.showToast('Broadcast-Intervall: 50-60000ms', 'warning');
            return;
        }

        var msg = { type: 'set_sim_interval' };
        if (updateMs) msg.update_interval_ms = updateMs;
        if (broadcastMs) msg.broadcast_interval_ms = broadcastMs;

        var success = this.sendMessage(msg);
        if (success) {
            this.showToast(
                'Intervalle gesetzt: Sim=' + (updateMs || '-') + 'ms, Broadcast=' + (broadcastMs || '-') + 'ms',
                'success'
            );
        }
    },

    // ========================================================================
    // THROTTLED UI UPDATES - Verhindert Performance-Probleme bei vielen Updates
    // ========================================================================

    scheduleGridUpdate: function() {
        /**
         * Throttled Grid-Update: Maximal alle 500ms.
         * Verhindert, dass bei vielen Registry-Updates die Grids
         * staendig neu gerendert werden.
         */
        if (this._gridUpdatePending) return;
        this._gridUpdatePending = true;
        var self = this;
        this._gridUpdateTimer = setTimeout(function() {
            self._gridUpdatePending = false;
            self.updateAllUI();
        }, 500);
    },

    scheduleChartUpdate: function() {
        /**
         * Throttled Chart-Update: Maximal alle 300ms.
         */
        if (this._uiUpdatePending) return;
        this._uiUpdatePending = true;
        var self = this;
        this._uiUpdateTimer = setTimeout(function() {
            self._uiUpdatePending = false;
            self.updateCharts();
        }, 300);
    },

    // ========================================================================
    // UI UPDATE FUNCTIONS
    // ========================================================================

    updateElement: function(id, value) {
        var el = document.getElementById(id);
        if (el) {
            el.textContent = value;
        }
    },

    updateConnectionStatus: function(connected) {
        var statusEl = document.getElementById('connectionStatus');
        if (!statusEl) return;

        var dotEl = statusEl.querySelector('.status-dot');
        var textEl = statusEl.querySelector('.status-text');

        if (connected) {
            statusEl.classList.remove('offline');
            statusEl.classList.add('online');
            if (textEl) textEl.textContent = 'Verbunden';
        } else {
            statusEl.classList.remove('online');
            statusEl.classList.add('offline');
            if (textEl) textEl.textContent = 'Getrennt';
        }
    },

    updateAllUI: function() {
        this.syncServerConfigUI();
        this.updateDeviceGrids();
        this.updateDeviceSelects();
        this.updateDeviceCount();
        this.updateCharts();
    },

    updateDeviceCount: function() {
        var sensorCount = Object.keys(this.state.sensors).length;
        var actuatorCount = Object.keys(this.state.actuators).length;

        this.updateElement('sensorCount', sensorCount);
        this.updateElement('actuatorCount', actuatorCount);
        this.updateElement('activeDeviceCount', (sensorCount + actuatorCount) + ' Devices');
    },

    updateDeviceValue: function(deviceId, value) {
        var valueEl = document.querySelector('[data-device-id="' + deviceId + '"] .device-value');
        if (valueEl) {
            var displayValue = (typeof value === 'number') ? Math.round(value) : value;
            // Update only when the value actually changed
            if (valueEl.textContent !== String(displayValue)) {
                valueEl.textContent = displayValue;
                valueEl.classList.add('updated');
                setTimeout(function() {
                    valueEl.classList.remove('updated');
                }, 300);
            }
        }
    },

    // ========================================================================
    // DEVICE GRIDS
    // ========================================================================

    updateDeviceGrids: function() {
        this.updateSensorGrid();
        this.updateActuatorGrid();
        this.updateOverviewDevices();
    },

    updateSensorGrid: function() {
        var grid = document.getElementById('sensorGrid');
        if (!grid) return;

        var sensors = Object.values(this.state.sensors);

        if (sensors.length === 0) {
            grid.innerHTML = this.createEmptyState('No sensors registered',
                'Sensors are registered automatically on Modbus read requests.');
            return;
        }

        grid.innerHTML = sensors.map(this.createSensorCard.bind(this)).join('');
    },

    updateActuatorGrid: function() {
        var grid = document.getElementById('actuatorGrid');
        if (!grid) return;

        var actuators = Object.values(this.state.actuators);

        if (actuators.length === 0) {
            grid.innerHTML = this.createEmptyState('No actuators registered',
                'Actuators are registered automatically on Modbus write requests.');
            return;
        }

        grid.innerHTML = actuators.map(this.createActuatorCard.bind(this)).join('');
    },

    updateOverviewDevices: function() {
        var grid = document.getElementById('overviewDevices');
        if (!grid) return;

        var sensors = Object.values(this.state.sensors).slice(0, 4);
        var actuators = Object.values(this.state.actuators).slice(0, 4);
        var devices = sensors.concat(actuators);

        if (devices.length === 0) {
            grid.innerHTML = this.createEmptyState('Warte auf Modbus-Anfragen',
                'Devices are registered automatically when they are requested.');
            return;
        }

        var self = this;
        grid.innerHTML = devices.map(function(d) {
            return d.id.startsWith('sensor') ? self.createSensorCard(d) : self.createActuatorCard(d);
        }).join('');
    },

    createSensorCard: function(sensor) {
        var simMode = sensor.simulation_mode || 'random';
        var value = (sensor.value !== undefined && sensor.value !== null) ? sensor.value : 0;
        return '<div class="device-card" data-device-id="' + sensor.id + '">' +
            '<div class="device-header">' +
                '<div class="device-type">' +
                    '<div class="device-type-icon sensor">S</div>' +
                    '<div class="device-info">' +
                        '<div class="device-id">' + this.escapeHtml(sensor.id) + '</div>' +
                        '<div class="device-address">Addr: ' + sensor.address + ' | ' + sensor.function_type + '</div>' +
                    '</div>' +
                '</div>' +
                '<span class="sim-mode-badge">' + simMode + '</span>' +
            '</div>' +
            '<div class="device-body">' +
                '<div class="device-value">' + Math.round(value) + '</div>' +
                '<div class="device-meta">' +
                    '<span>Reads: ' + (sensor.read_count || 0) + '</span>' +
                    '<span>' + this.formatTime(sensor.last_read) + '</span>' +
                '</div>' +
            '</div>' +
            '<div class="device-controls">' +
                '<button class="btn btn-small btn-secondary" onclick="App.openDeviceConfig(\'' + sensor.id + '\')">Konfig</button>' +
                '<button class="btn btn-small btn-secondary" onclick="App.setManualValue(\'' + sensor.id + '\')">Setzen</button>' +
                '<button class="btn btn-small btn-secondary" onclick="App.requestHistory(\'' + sensor.id + '\')">History</button>' +
            '</div>' +
        '</div>';
    },

    createActuatorCard: function(actuator) {
        var value = (actuator.value !== undefined && actuator.value !== null) ? actuator.value : 0;
        var simMode = actuator.simulation_mode || '';
        var modeHtml = simMode
            ? '<span class="sim-mode-badge">' + simMode + '</span>'
            : '';
        return '<div class="device-card" data-device-id="' + actuator.id + '">' +
            '<div class="device-header">' +
                '<div class="device-type">' +
                    '<div class="device-type-icon actuator">A</div>' +
                    '<div class="device-info">' +
                        '<div class="device-id">' + this.escapeHtml(actuator.id) + '</div>' +
                        '<div class="device-address">Addr: ' + actuator.address + ' | ' + actuator.function_type + '</div>' +
                    '</div>' +
                '</div>' +
                modeHtml +
            '</div>' +
            '<div class="device-body">' +
                '<div class="device-value">' + Math.round(value) + '</div>' +
                '<div class="device-meta">' +
                    '<span>Writes: ' + (actuator.write_count || 0) + '</span>' +
                    '<span>' + this.formatTime(actuator.last_write) + '</span>' +
                '</div>' +
            '</div>' +
            '<div class="device-controls">' +
                '<button class="btn btn-small btn-secondary" onclick="App.requestHistory(\'' + actuator.id + '\')">History</button>' +
            '</div>' +
        '</div>';
    },

    createEmptyState: function(title, description) {
        return '<div class="empty-state">' +
            '<div class="empty-icon">---</div>' +
            '<h4>' + title + '</h4>' +
            '<p>' + description + '</p>' +
        '</div>';
    },

    // ========================================================================
    // DEVICE SELECTS
    // ========================================================================

    updateDeviceSelects: function() {
        var sensorIds = Object.keys(this.state.sensors);
        var actuatorIds = Object.keys(this.state.actuators);
        var allDeviceIds = sensorIds.concat(actuatorIds);

        this.populateSelect('simDeviceSelect', sensorIds, '-- Select sensor --');
        this.populateSelect('overviewChartDevice', allDeviceIds, '-- Select device --');
        this.populateSelect('sensorChartSelect', sensorIds, '-- Select sensor --');
        this.populateSelect('actuatorChartSelect', actuatorIds, '-- Select actuator --');
    },

    populateSelect: function(selectId, options, placeholder) {
        var select = document.getElementById(selectId);
        if (!select) return;

        var currentValue = select.value;
        select.innerHTML = '<option value="">' + placeholder + '</option>' +
            options.map(function(id) {
                return '<option value="' + id + '">' + id + '</option>';
            }).join('');

        if (currentValue && options.indexOf(currentValue) !== -1) {
            select.value = currentValue;
        }
    },

    // ========================================================================
    // CHARTS
    // ========================================================================

    initCharts: function() {
        var chartOptions = {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 0 },
            scales: {
                x: {
                    type: 'linear',
                    display: true,
                    title: { display: true, text: 'Zeit', color: '#8b949e' },
                    ticks: {
                        color: '#8b949e',
                        callback: function(value) {
                            return App.formatChartTimestamp(value, false);
                        },
                        maxTicksLimit: 6
                    },
                    grid: { color: 'rgba(139, 148, 158, 0.1)' }
                },
                y: {
                    display: true,
                    title: { display: true, text: 'Wert', color: '#8b949e' },
                    ticks: { color: '#8b949e' },
                    grid: { color: 'rgba(139, 148, 158, 0.1)' }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: function(items) {
                            if (!items || items.length === 0) {
                                return '';
                            }
                            return App.formatChartTimestamp(items[0].parsed.x, true);
                        }
                    }
                }
            }
        };

        var overviewCtx = document.getElementById('overviewChart');
        if (overviewCtx) {
            this.charts.overview = new Chart(overviewCtx, {
                type: 'line',
                data: { datasets: [{ label: 'Wert', data: [], borderColor: '#58a6ff', backgroundColor: 'rgba(88, 166, 255, 0.1)', fill: true, tension: 0.4 }] },
                options: chartOptions
            });
        }

        var sensorCtx = document.getElementById('sensorChart');
        if (sensorCtx) {
            this.charts.sensor = new Chart(sensorCtx, {
                type: 'line',
                data: { datasets: [{ label: 'Wert', data: [], borderColor: '#3fb950', backgroundColor: 'rgba(63, 185, 80, 0.1)', fill: true, tension: 0.4 }] },
                options: chartOptions
            });
        }

        var actuatorCtx = document.getElementById('actuatorChart');
        if (actuatorCtx) {
            this.charts.actuator = new Chart(actuatorCtx, {
                type: 'line',
                data: { datasets: [{ label: 'Wert', data: [], borderColor: '#d29922', backgroundColor: 'rgba(210, 153, 34, 0.1)', fill: true, tension: 0.4 }] },
                options: chartOptions
            });
        }
    },

    addChartDatapoint: function(deviceId, value, timestampMs) {
        this.appendHistorySamples(deviceId, [{ timestamp_ms: timestampMs || Date.now(), value: value }]);

        // Chart-Update wird via scheduleChartUpdate gesteuert
    },

    updateCharts: function() {
        var overviewSelect = document.getElementById('overviewChartDevice');
        var sensorSelect = document.getElementById('sensorChartSelect');
        var actuatorSelect = document.getElementById('actuatorChartSelect');

        if (overviewSelect && this.charts.overview) {
            var deviceId = overviewSelect.value;
            this.charts.overview.data.datasets[0].data = deviceId ? this.getChartSeries(deviceId) : [];
            this.charts.overview.update('none');
        }

        if (sensorSelect && this.charts.sensor) {
            var sensorDeviceId = sensorSelect.value;
            this.charts.sensor.data.datasets[0].data = sensorDeviceId ? this.getChartSeries(sensorDeviceId) : [];
            this.charts.sensor.update('none');
        }

        if (actuatorSelect && this.charts.actuator) {
            var actuatorDeviceId = actuatorSelect.value;
            this.charts.actuator.data.datasets[0].data = actuatorDeviceId ? this.getChartSeries(actuatorDeviceId) : [];
            this.charts.actuator.update('none');
        }
    },

    requestHistory: function(deviceId) {
        this.sendMessage({ type: 'get_history', device_id: deviceId });
    },

    // ========================================================================
    // SIMULATION
    // ========================================================================

    applySimulation: function() {
        var deviceSelect = document.getElementById('simDeviceSelect');
        var deviceId = deviceSelect ? deviceSelect.value : '';

        if (!deviceId) {
            this.showToast('Please select a device', 'warning');
            return;
        }

        var config = this.buildSimulationConfig();
        this.sendSimulationConfig(deviceId, config);
    },

    applySimulationToAll: function() {
        var config = this.buildSimulationConfig();
        var sensorIds = Object.keys(this.state.sensors);

        if (sensorIds.length === 0) {
            this.showToast('No sensors available', 'warning');
            return;
        }

        this.sendMessage({
            type: 'bulk_set_simulation',
            devices: sensorIds,
            config: config
        });

        this.showToast('Simulation applied to ' + sensorIds.length + ' sensors', 'success');
    },

    buildSimulationConfig: function() {
        var modeSelect = document.getElementById('simModeSelect');
        var mode = modeSelect ? modeSelect.value : 'random';
        var config = { mode: mode };

        switch(mode) {
            case 'random':
                config.min = this.getNumberValue('simMin', 0);
                config.max = this.getNumberValue('simMax', 65535);
                break;
            case 'constant':
                config.value = this.getNumberValue('simConstant', 0);
                break;
            case 'pid':
                config.setpoint = this.getNumberValue('simSetpoint', 50);
                config.disturbance = this.getNumberValue('simDisturbance', 0);
                config.kp = this.getNumberValue('pidKp', 1.0);
                config.ki = this.getNumberValue('pidKi', 0.1);
                config.kd = this.getNumberValue('pidKd', 0.05);
                break;
            case 'ramp':
                config.start = this.getNumberValue('simRampStart', 0);
                config.end = this.getNumberValue('simRampEnd', 65535);
                config.duration_ms = this.getNumberValue('simRampDuration', 10000);
                config.loop = true;
                break;
            case 'sine':
                config.amplitude = this.getNumberValue('simAmplitude', 32767);
                config.offset = this.getNumberValue('simOffset', 32767);
                config.frequency = this.getNumberValue('simFrequency', 0.1);
                break;
            case 'noise':
                config.base = this.getNumberValue('simNoiseBase', 32767);
                config.noise_level = this.getNumberValue('simNoiseLevel', 1000);
                break;
            case 'error':
                var errorTypeEl = document.getElementById('simErrorType');
                config.error_type = errorTypeEl ? errorTypeEl.value : 'stuck';
                break;
            case 'manual':
                config.value = this.getNumberValue('simManualValue', 0);
                break;
        }

        return config;
    },

    getNumberValue: function(id, defaultValue) {
        var el = document.getElementById(id);
        return el ? parseFloat(el.value) || defaultValue : defaultValue;
    },

    sendSimulationConfig: function(deviceId, config) {
        var success = this.sendMessage({
            type: 'set_simulation',
            device_id: deviceId,
            config: config
        });

        if (success) {
            this.showToast('Simulation konfiguriert: ' + config.mode, 'success');
        }
    },

    showSimulationFields: function() {
        var modeSelect = document.getElementById('simModeSelect');
        var container = document.getElementById('simConfigFields');
        if (!modeSelect || !container) return;

        var mode = modeSelect.value;

        var fields = {
            random: this.createFormRow([
                this.createFormGroup('Min-Wert', 'simMin', 'number', '0'),
                this.createFormGroup('Max-Wert', 'simMax', 'number', '65535')
            ]),
            constant: this.createFormRow([
                this.createFormGroup('Constant value', 'simConstant', 'number', '32767')
            ]),
            pid: this.createFormRow([
                this.createFormGroup('Setpoint', 'simSetpoint', 'number', '50'),
                this.createFormGroup('Disturbance', 'simDisturbance', 'number', '0')
            ]),
            ramp: this.createFormRow([
                this.createFormGroup('Start', 'simRampStart', 'number', '0'),
                this.createFormGroup('Ende', 'simRampEnd', 'number', '65535'),
                this.createFormGroup('Duration (ms)', 'simRampDuration', 'number', '10000')
            ]),
            sine: this.createFormRow([
                this.createFormGroup('Amplitude', 'simAmplitude', 'number', '32767'),
                this.createFormGroup('Offset', 'simOffset', 'number', '32767'),
                this.createFormGroup('Frequency (Hz)', 'simFrequency', 'number', '0.1')
            ]),
            noise: this.createFormRow([
                this.createFormGroup('Base value', 'simNoiseBase', 'number', '32767'),
                this.createFormGroup('Noise level', 'simNoiseLevel', 'number', '1000')
            ]),
            error: '<div class="form-group"><label class="form-label">Error type</label>' +
                '<select id="simErrorType" class="form-input">' +
                '<option value="stuck">Stuck</option>' +
                '<option value="overflow">Overflow</option>' +
                '<option value="underflow">Underflow</option>' +
                '<option value="spike">Spikes</option>' +
                '<option value="dropout">Dropouts</option>' +
                '</select></div>',
            manual: this.createFormRow([
                this.createFormGroup('Manual value', 'simManualValue', 'number', '0')
            ])
        };

        container.innerHTML = fields[mode] || '';
    },

    createFormRow: function(groups) {
        return '<div class="form-row">' + groups.join('') + '</div>';
    },

    createFormGroup: function(label, id, type, defaultValue) {
        return '<div class="form-group">' +
            '<label class="form-label">' + label + '</label>' +
            '<input type="' + type + '" id="' + id + '" value="' + defaultValue + '" class="form-input">' +
        '</div>';
    },

    setManualValue: function(deviceId) {
        var value = prompt('Enter new value:', '0');
        if (value !== null) {
            var parsedValue = parseFloat(value);
            if (!isFinite(parsedValue)) {
                this.showToast('Invalid value', 'warning');
                return;
            }
            var success = this.sendMessage({ type: 'set_value', device_id: deviceId, value: parsedValue });
            if (success) {
                this.showToast('Manual value set', 'success');
            }
        }
    },

    // Quick Actions
    setAllSensorsRandom: function() {
        this.sendMessage({
            type: 'bulk_set_simulation',
            devices: Object.keys(this.state.sensors),
            config: { mode: 'random', min: 0, max: 65535 }
        });
        this.showToast('All sensors set to random', 'success');
    },

    setAllSensorsSine: function() {
        this.sendMessage({
            type: 'bulk_set_simulation',
            devices: Object.keys(this.state.sensors),
            config: { mode: 'sine', amplitude: 32767, offset: 32767, frequency: 0.1 }
        });
        this.showToast('All sensors set to sine', 'success');
    },

    setAllSensorsConstant: function() {
        var value = prompt('Enter a constant value for all sensors:', '32767');
        if (value !== null) {
            this.sendMessage({
                type: 'bulk_set_simulation',
                devices: Object.keys(this.state.sensors),
                config: { mode: 'constant', value: parseFloat(value) }
            });
            this.showToast('All sensors set to constant', 'success');
        }
    },

    resetAllSimulations: function() {
        if (confirm('Reset all simulations?')) {
            this.sendMessage({
                type: 'bulk_set_simulation',
                devices: Object.keys(this.state.sensors),
                config: { mode: 'random', min: 0, max: 65535 }
            });
            this.showToast('All simulations reset', 'success');
        }
    },

    // ========================================================================
    // MODAL
    // ========================================================================

    openDeviceConfig: function(deviceId) {
        this.state.selectedDevice = deviceId;
        var sensor = this.state.sensors[deviceId];

        var modalTitle = document.getElementById('modalTitle');
        var modalBody = document.getElementById('modalBody');

        if (modalTitle) modalTitle.textContent = 'Configuration: ' + deviceId;

        if (modalBody) {
            var self = this;
            var modeOptions = Object.keys(this.state.simulationModes).map(function(key) {
                var label = self.state.simulationModes[key];
                var selected = sensor && sensor.simulation_mode === key ? ' selected' : '';
                return '<option value="' + key + '"' + selected + '>' + label + '</option>';
            }).join('');

            var currentConfig = (sensor && sensor.config) ? sensor.config : {};

            modalBody.innerHTML =
                '<div class="form-group">' +
                    '<label class="form-label">Simulationsmodus</label>' +
                    '<select id="modalSimMode" class="form-input">' + modeOptions + '</select>' +
                '</div>' +
                '<div class="form-group">' +
                    '<label class="form-label">Min-Wert</label>' +
                    '<input type="number" id="modalMin" class="form-input" value="' + (currentConfig.min || 0) + '">' +
                '</div>' +
                '<div class="form-group">' +
                    '<label class="form-label">Max-Wert</label>' +
                    '<input type="number" id="modalMax" class="form-input" value="' + (currentConfig.max || 65535) + '">' +
                '</div>';
        }

        var modal = document.getElementById('deviceModal');
        if (modal) modal.classList.add('active');
    },

    closeModal: function() {
        var modal = document.getElementById('deviceModal');
        if (modal) modal.classList.remove('active');
        this.state.selectedDevice = null;
    },

    saveDeviceConfig: function() {
        if (!this.state.selectedDevice) return;

        var modeEl = document.getElementById('modalSimMode');
        var minEl = document.getElementById('modalMin');
        var maxEl = document.getElementById('modalMax');

        var config = {
            mode: modeEl ? modeEl.value : 'random',
            min: minEl ? parseFloat(minEl.value) : 0,
            max: maxEl ? parseFloat(maxEl.value) : 65535
        };

        this.sendSimulationConfig(this.state.selectedDevice, config);
        this.closeModal();
    },

    // ========================================================================
    // THEME
    // ========================================================================

    toggleTheme: function() {
        var newTheme = this.state.theme === 'dark' ? 'light' : 'dark';
        this.setTheme(newTheme);
        this.sendMessage({ type: 'set_theme', theme: newTheme });
    },

    setTheme: function(theme) {
        this.state.theme = theme;
        document.body.setAttribute('data-theme', theme);
        localStorage.setItem('modbus_theme', theme);

        var themeSelect = document.getElementById('themeSelect');
        if (themeSelect) themeSelect.value = theme;
    },

    // ========================================================================
    // TABS
    // ========================================================================

    initTabs: function() {
        var self = this;
        document.querySelectorAll('.nav-tab').forEach(function(tab) {
            tab.addEventListener('click', function() {
                var tabId = this.getAttribute('data-tab');
                self.switchTab(tabId);
            });
        });
    },

    switchTab: function(tabId) {
        document.querySelectorAll('.nav-tab').forEach(function(t) {
            t.classList.remove('active');
        });
        document.querySelectorAll('.tab-content').forEach(function(c) {
            c.classList.remove('active');
        });

        var activeTab = document.querySelector('.nav-tab[data-tab="' + tabId + '"]');
        var activeContent = document.getElementById('tab-' + tabId);

        if (activeTab) activeTab.classList.add('active');
        if (activeContent) activeContent.classList.add('active');
    },

    // ========================================================================
    // EVENT LISTENERS
    // ========================================================================

    initEventListeners: function() {
        var self = this;

        // Simulation Mode Select
        var simModeSelect = document.getElementById('simModeSelect');
        if (simModeSelect) {
            simModeSelect.addEventListener('change', function() {
                self.showSimulationFields();
            });
            this.showSimulationFields();
        }

        // Theme Select
        var themeSelect = document.getElementById('themeSelect');
        if (themeSelect) {
            themeSelect.addEventListener('change', function() {
                self.setTheme(this.value);
                self.sendMessage({ type: 'set_theme', theme: this.value });
            });
        }

        // Chart Selects
        ['overviewChartDevice', 'sensorChartSelect', 'actuatorChartSelect'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', function() {
                    if (this.value && (!self.state.history[this.value] || self.state.history[this.value].length === 0)) {
                        self.requestHistory(this.value);
                    }
                    self.updateCharts();
                });
            }
        });

        var chartHistory = document.getElementById('chartHistory');
        if (chartHistory) {
            chartHistory.addEventListener('change', function() {
                self.updateCharts();
            });
        }

        // Filter Inputs
        var sensorFilter = document.getElementById('sensorFilter');
        if (sensorFilter) {
            sensorFilter.addEventListener('input', function() {
                self.filterDevices('sensorGrid', this.value, self.state.sensors);
            });
        }

        var actuatorFilter = document.getElementById('actuatorFilter');
        if (actuatorFilter) {
            actuatorFilter.addEventListener('input', function() {
                self.filterDevices('actuatorGrid', this.value, self.state.actuators);
            });
        }
    },

    filterDevices: function(gridId, filter, devices) {
        var grid = document.getElementById(gridId);
        if (!grid) return;

        var filtered = Object.values(devices).filter(function(d) {
            return d.id.toLowerCase().indexOf(filter.toLowerCase()) !== -1;
        });

        if (gridId === 'sensorGrid') {
            grid.innerHTML = filtered.map(this.createSensorCard.bind(this)).join('');
        } else {
            grid.innerHTML = filtered.map(this.createActuatorCard.bind(this)).join('');
        }
    },

    // ========================================================================
    // LOGGING
    // ========================================================================

    addLog: function(type, message) {
        var logContainer = document.getElementById('eventLog');
        if (!logContainer) return;

        var now = new Date();
        var time = now.toLocaleTimeString('en-US');

        var typeClass = 'log-' + type.toLowerCase();

        var entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = '<span class="log-time">' + time + '</span>' +
            '<span class="log-type ' + typeClass + '">' + type + '</span>' +
            '<span class="log-message">' + this.escapeHtml(message) + '</span>';

        logContainer.insertBefore(entry, logContainer.firstChild);

        this.state.logs.push({ time: now.toISOString(), type: type, message: message });

        while (logContainer.children.length > 200) {
            logContainer.removeChild(logContainer.lastChild);
        }
        while (this.state.logs.length > 500) {
            this.state.logs.shift();
        }
    },

    clearLogs: function() {
        var logContainer = document.getElementById('eventLog');
        if (logContainer) {
            logContainer.innerHTML = '<div class="log-entry">' +
                '<span class="log-time">--:--:--</span>' +
                '<span class="log-type log-info">INFO</span>' +
                '<span class="log-message">Logs geloescht</span>' +
            '</div>';
        }
        this.state.logs = [];
        this.showToast('Logs geloescht', 'success');
    },

    exportLogs: function() {
        var csv = 'timestamp,type,message\n' +
            this.state.logs.map(function(l) {
                return l.time + ',' + l.type + ',"' + l.message.replace(/"/g, '""') + '"';
            }).join('\n');

        this.downloadFile(csv, 'modbus_logs_' + Date.now() + '.csv', 'text/csv');
        this.showToast('Logs exported', 'success');
    },

    // ========================================================================
    // EXPORT FUNCTIONS
    // ========================================================================

    exportConfig: function() {
        var config = {
            sensors: this.state.sensors,
            actuators: this.state.actuators,
            server_config: this.state.config,
            exported_at: new Date().toISOString()
        };

        this.downloadFile(JSON.stringify(config, null, 2), 'modbus_config_' + Date.now() + '.json', 'application/json');
        this.showToast('Configuration exported', 'success');
    },

    exportSensorData: function() {
        var sensorSelect = document.getElementById('sensorChartSelect');
        var sensorId = sensorSelect ? sensorSelect.value : '';

        if (!sensorId || !this.state.history[sensorId]) {
            this.showToast('Please select a sensor', 'warning');
            return;
        }

        var csv = 'timestamp_iso,timestamp_ms,value\n' +
            this.state.history[sensorId].map(function(p) {
                return '"' + new Date(p.x).toISOString() + '",' + p.x + ',' + p.y;
            }).join('\n');

        this.downloadFile(csv, sensorId + '_data_' + Date.now() + '.csv', 'text/csv');
        this.showToast('Sensor data exported', 'success');
    },

    downloadFile: function(content, filename, mimeType) {
        var blob = new Blob([content], { type: mimeType });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    },

    // ========================================================================
    // RESET FUNCTIONS
    // ========================================================================

    resetAllDevices: function() {
        if (!confirm('Reset all devices? This deletes all histories and configurations.')) {
            return;
        }

        var self = this;
        Object.keys(this.state.sensors).forEach(function(id) {
            self.sendMessage({ type: 'reset_device', device_id: id });
        });
        Object.keys(this.state.actuators).forEach(function(id) {
            self.sendMessage({ type: 'reset_device', device_id: id });
        });

        this.state.history = {};
        this.showToast('All devices reset', 'success');
    },

    // ========================================================================
    // TOAST NOTIFICATIONS
    // ========================================================================

    showToast: function(message, type) {
        var container = document.getElementById('toastContainer');
        if (!container) return;

        var icons = { success: '[OK]', error: '[X]', warning: '[!]', info: '[i]' };

        var toast = document.createElement('div');
        toast.className = 'toast ' + type;
        toast.innerHTML = '<span class="toast-icon">' + (icons[type] || '[i]') + '</span>' +
            '<span class="toast-message">' + this.escapeHtml(message) + '</span>';

        container.appendChild(toast);

        setTimeout(function() {
            toast.remove();
        }, 4000);
    },

    // ========================================================================
    // POLLING
    // ========================================================================

    startStatsPolling: function() {
        var self = this;
        setInterval(function() {
            if (self.state.connected) {
                self.sendMessage({ type: 'get_stats' });
            }
        }, 2000);
    },

    // ========================================================================
    // UTILITY FUNCTIONS
    // ========================================================================

    formatNumber: function(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toString();
    },

    formatUptime: function(seconds) {
        if (seconds < 60) return Math.floor(seconds) + 's';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + Math.floor(seconds % 60) + 's';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
        return Math.floor(seconds / 86400) + 'd ' + Math.floor((seconds % 86400) / 3600) + 'h';
    },

    formatTime: function(isoString) {
        if (!isoString) return 'Never';
        try {
            var date = new Date(isoString);
            return date.toLocaleTimeString('en-US');
        } catch (e) {
            return 'Error';
        }
    },

    escapeHtml: function(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

// ============================================================================
// INITIALIZE ON DOM READY
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    App.init();
});
