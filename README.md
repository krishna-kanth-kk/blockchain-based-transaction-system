# UPI Chain — Local Blockchain Payment Demo

A runnable educational prototype showing a UPI-style payment flow with:
- SHA-256 hash-linked blocks
- ECDSA P-256 transaction/block signatures
- A local Proof-of-Authority-style validator quorum simulation
- SQLite persistence and demo balances
- Flask API + browser dashboard with 5-second live refresh
- Ledger integrity verification and an intentional tamper demonstration
- Atomic balance + ledger updates using integer paise/cents
- Required idempotency keys to prevent duplicate payment retries
- Session CSRF protection and safer session-cookie defaults
- SQLite WAL mode, constraints and migration from the original schema
- Signed MetaMask wallet linking using a one-time server challenge
- Wallet/network records and transaction event/audit history

## Important scope and safety note

This is **not connected to UPI, NPCI, any bank, PSP, payment gateway, or real money**. It does not initiate or settle actual payments. "Real-time" means the local dashboard refreshes its demo data every five seconds. The three validators are simulated identities in one Python process, not independent distributed nodes. It is suitable for a local demonstration and as a foundation for integrating a regulated sandbox adapter; it is not production financial infrastructure by itself.

## Requirements

- Python 3.10 or newer recommended
- Windows, macOS, or Linux
- Internet is not required after dependencies are installed

## Run on Windows

1. Extract `upi_blockchain_project.zip`.
2. Open Command Prompt or PowerShell in the extracted `upi_blockchain_project` folder.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .venv\Scripts\activate
   ```

4. Install packages:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. Start the server:

   ```powershell
   python app.py
   ```

6. Open **http://127.0.0.1:5000** in your browser.

## Run on macOS / Linux

```bash
cd upi_blockchain_project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

## Demo login

All demo users use PIN `1234`.

| User | UPI ID | Starting demo balance |
|---|---|---:|
| Alice | alice@demo | ₹5,000 |
| Bob | bob@demo | ₹5,000 |
| Charlie | charlie@demo | ₹5,000 |

Sign in as Alice and send a payment to Bob. Sign out and sign in as Bob to see the incoming payment and updated demo balance.

## Demonstration flow

1. Sign in as Alice.
2. Select Bob in **New payment**, enter an amount and send.
3. Open **Blockchain ledger** to inspect block index, timestamp, hash, previous hash and validator approvals.
4. Select **Verify entire ledger**. The server recomputes each hash and checks the ECDSA signature.
5. Select **Simulate tampering** on Overview. The demo deliberately edits a stored transaction in SQLite; verification should fail.
6. Select **Reset demo data** to clear the ledger and restore all demo balances to ₹5,000.

## Project structure

```text
upi_blockchain_project/
├── app.py                 # Flask routes, blockchain, signatures, SQLite
├── requirements.txt
├── README.md
├── upi_blockchain.db      # Created automatically on first run
├── keys/                  # ECDSA key pairs created on first login
├── templates/
│   └── index.html         # Dashboard layout
└── static/
    ├── app.js             # API calls, UI and 5-second refresh
    └── style.css          # Dashboard styling
```

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/login` | Demo username + PIN login |
| POST | `/api/logout` | End demo session |
| GET | `/api/session` | Current user and balance |
| GET | `/api/users` | Other demo recipients |
| POST | `/api/pay` | Create, sign and append a demo payment; requires `X-CSRF-Token` and `Idempotency-Key` |
| GET | `/api/csrf` | Issue the current session CSRF token |
| GET | `/api/wallet` | Read the linked wallet and network |
| POST | `/api/wallet/challenge` | Create a short-lived wallet signing challenge |
| POST | `/api/wallet/connect` | Verify a MetaMask signature and link the wallet |
| DELETE | `/api/wallet` | Disconnect the linked wallet |
| GET | `/api/transactions/<id>` | Read a transaction receipt, block proof and events |
| GET | `/api/transactions` | Current user's payment history |
| GET | `/api/chain` | Read public block fields |
| GET | `/api/verify` | Verify hash links and ECDSA signatures |
| GET | `/api/analytics` | Transaction count, volume and integrity |
| POST | `/api/demo/tamper` | Intentionally corrupt a stored transaction |
| POST | `/api/demo/reset` | Reset ledger and demo balances |

## Important limitations / improvements for a serious project

- Replace demo PIN authentication with a proper identity provider, password/PIN hashing, rate limiting and MFA.
- Never store private keys unencrypted in a production deployment; use an HSM/KMS or secure wallet.
- Use independent validator services and a real, formally specified consensus protocol.
- Add idempotency keys, replay protection, transaction state machines, concurrency control, audit logging and robust error handling.
- Real UPI integration requires regulated bank/PSP/NPCI participation and approved APIs/certifications. A student project should use a sandbox or simulated payment adapter.
- The current demo uses a single local SQLite database and is intended for one-machine demonstration only.
- Add a regulated payment adapter for any real settlement flow; that adapter must own bank/PSP authentication, webhooks, reconciliation and compliance controls.

## GitHub and Render deployment

This repository includes a Procfile and render.yaml. The demo uses SQLite, so hosted data is not durable across redeploys; use managed PostgreSQL or object storage for persistent production data.

PowerShell commands:

    git init -b main
    git add .
    git commit -m "Initial BlockPay demo"
    git remote add origin https://github.com/YOUR_USERNAME/blockpay.git
    git push -u origin main

On Render, choose New > Blueprint, select the GitHub repository, and deploy the included render.yaml. The APP_SECRET value is generated automatically.
"# blockchain-based-transaction-system" 
## Live Demo

[Open Blockchain Payment Website](https://blockchain-based-transaction-system.onrender.com/)
