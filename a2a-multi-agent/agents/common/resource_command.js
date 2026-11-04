// One fresh IoTAuth client per command. The accessor keeps session state at module scope.
'use strict';

const path = require('path');
const fs = require('fs');
const entityDirectory = process.env.IOTAUTH_ENTITY_DIR ||
    path.resolve(__dirname, '../../../iotauth/entity/node/example_entities');
const SecureCommClient = require(path.resolve(entityDirectory, '../accessors/SecureCommClient'));
const [config, group, host, portText, command] = process.argv.slice(2);
const replies = new Map([['RETRIEVE_ITEM', 'retrieved'], ['OPEN_GATE', 'open']]);
const expected = replies.get(command);
if (!expected) process.exit(1);
const port = Number(portText);
if (!host || !/^\d+$/.test(portText) || !Number.isInteger(port) || port < 1 || port > 65535) process.exit(2);

// A quiet disconnect or an event loop ending without a reply must fail.
process.exitCode = 1;
const client = new SecureCommClient(config);
client.initialize();

const metrics = [];
const now = () => process.hrtime.bigint();
const elapsed = start => Number(now() - start) / 1e6;
function record(metric) {
    metrics.push(metric);
    if (process.env.IOTAUTH_LATENCY_FILE) {
        try {
            fs.writeFileSync(process.env.IOTAUTH_LATENCY_FILE, JSON.stringify({events: metrics}));
        } catch (_) { /* Measurement must not change secure command behavior. */ }
    }
}
let handshakeStarted;
let commandStarted;
let connectStarted;
client.setOutputHandler('latency', metric => {
    record(metric);
    if (metric.name === 'sst_session_key' && metric.status === 'ok') handshakeStarted = now();
});
let sent = false;
client.setOutputHandler('error', () => process.exit(1));
client.setOutputHandler('connected', connected => {
    if (!connected || sent) process.exit(1);
    record({name: 'sst_connect', duration_ms: elapsed(connectStarted), status: 'ok'});
    if (handshakeStarted !== undefined) {
        record({name: 'sst_handshake', duration_ms: elapsed(handshakeStarted), status: 'ok'});
    }
    // The underlying handshake switches to IN_COMM after this callback returns.
    setImmediate(() => {
        sent = true;
        commandStarted = now();
        client.provideInput('toSend', Buffer.from(command));
    });
});
client.setOutputHandler('received', data => {
    const valid = sent && Buffer.from(data).equals(Buffer.from(expected));
    if (commandStarted !== undefined) {
        record({name: 'sst_command_reply', duration_ms: elapsed(commandStarted), status: valid ? 'ok' : 'error'});
    }
    process.exit(valid ? 0 : 3);
});
connectStarted = now();
client.provideInput('serverHostPort', {host, port}, group);
