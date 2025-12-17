# How to Run

1. Create virtual environment:
```
python -m venv .venv
source .venv/bin/activate
```
2. Install Python dependencies
```
pip install -r requirements.txt
```

3. Create Session Key ID
TODO: add iotauth as submodule

4. Open terminal 1
```
python3 __main__.py
```
5. Open terminal 2
```
cd remote_a2a
python3 __main__.py
```
6. Open terminal 3
Modify SESSION_KEY_ID appropriately before you run the program.
```
python3 test_agent.py
```
