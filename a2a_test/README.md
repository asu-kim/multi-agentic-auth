# How to Run

```
export ROOT="/Users/<username>/multi-agentic-website"
```

1. Create virtual environment:
```
python -m venv .venv
source .venv/bin/activate
```

2. Install Python dependencies
```
cd $ROOT/a2a_test
pip install -r requirements.txt
```

3. Create Session Key ID

    3.1 Check `iotauth` submodule
    ```
    cd $ROOT/iotauth
    git submodule update --init --recursive
    git pull
    ```
    3.2 Open terminal 1
    ```
    # generate entities
    cd $ROOT/iotauth/examples
    ./initConfigs.sh
    ./cleanAll.sh
    ./generateAll.sh -g configs/agentAccess.graph 
    ```

    ```
    # start Auth
    cd $ROOT/iotauth/auth/auth-server
    make
    java -jar target/auth-server-jar-with-dependencies.jar -p ../properties/exampleAuth101.properties
    ```

    3.3 Open terminal 2
    ```
    # generate key for delegate access to agent
    cd $ROOT/iotauth/entity/node/example_entities
    node user.js configs/net1/user.config 
    ```
    Inside the program, enter the following command to delegate access.

    ```
    delegateAccess low
    ```
    Terminate the program after checking the `sessionKeyID`.

4. Open terminal 3

Run Agent1
```
source .venv/bin/activate
cd $ROOT/a2a_test
python3 __main__.py
```

5. Open terminal 4

Run Agent2
```
source .venv/bin/activate
cd $ROOT/remote_a2a
python3 __main__.py
```

6. Open terminal 5
Use the `sessionKeyID` that we checked in `3.3`.
```
source .venv/bin/activate
cd $ROOT/a2a_test
python3 test_agent.py --keyId sessionKeyID
```
