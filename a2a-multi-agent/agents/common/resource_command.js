// One fresh IoTAuth client per command. The accessor keeps session state at module scope.
'use strict';

const path = require('path');
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

let sent = false;
client.setOutputHandler('error', () => process.exit(1));
client.setOutputHandler('connected', connected => {
    if (!connected || sent) process.exit(1);
    // The underlying handshake switches to IN_COMM after this callback returns.
    setImmediate(() => {
        sent = true;
        client.provideInput('toSend', Buffer.from(command));
    });
});
client.setOutputHandler('received', data => {
    process.exit(sent && Buffer.from(data).equals(Buffer.from(expected)) ? 0 : 3);
});
client.provideInput('serverHostPort', {host, port}, group);
