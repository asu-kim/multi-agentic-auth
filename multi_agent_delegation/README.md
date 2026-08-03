# How to Run SST A2A Tests

For your convenience, you can set an environmental variable, `MAA_ROOT`,
to indicate the root directory to this repo for Multi Agentic Auth (MAA).

In the `multi-agentic-website` directory,

```
export MAA_ROOT=$(pwd)
```

## 1 Set up environments

### 1.1 Check out `iotauth` submodule
```
cd $MAA_ROOT/iotauth
git submodule update --init --recursive
```

### 1.2 Create a virtual environment:
```
python -m venv .venv
source .venv/bin/activate
```

### 1.2 Install Python dependencies
```
cd $MAA_ROOT/a2a_test
pip install -r requirements.txt
```

## 3 Create Session Key ID

### 3.1 Open terminal 1

When prompted, to create a password, enter your new password twice.

```
# generate entities
cd $MAA_ROOT/iotauth/examples
./initConfigs.sh
./cleanAll.sh
./generateAll.sh -g configs/agentAccess.graph 
```

When prompted, enter your password just created.

```
# start Auth
cd $MAA_ROOT/iotauth/auth/auth-server
make
java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties
```

### 3.2 Open terminal 2
```
# generate key for delegate access to agent
cd $MAA_ROOT/iotauth/entity/node/example_entities
node user.js configs/net1/user.config 
```
Inside the program, enter the following command to delegate access.

```
delegateAccess low
```
Terminate the program after checking the `sessionKeyID`.

## 4 Open terminal 3

Run Agent1
```
source .venv/bin/activate
cd $MAA_ROOT/a2a_test
python3 __main__.py
```

## 5 Open terminal 4

Run Agent2
```
source .venv/bin/activate
cd $MAA_ROOT/a2a_test/remote_a2a
python3 __main__.py
```

## 6 Open terminal 5

Replace the placeholder below, `[sessionKeyID]`, with the actual session key ID obtained from step 3.2 above.
```
source .venv/bin/activate
cd $MAA_ROOT/a2a_test
python3 test_agent.py --keyId [sessionKeyID]
```

If you see messages like below, your test is successful.
```
[Agent1] handshake complete
sessionKeyId=10100000
nonce1=d0bb76aed650008ab862fefc9e9e91e3
nonce2=ceea12af4bf5be0ab174dbeaf377784f
verify_hmac1_ok=True
agent2_verify_reply=HMAC2 verified
```
