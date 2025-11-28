import os
import json
import hashlib
from web3 import Web3
from dotenv import load_dotenv
from typing import Dict, Any

load_dotenv()

# --- Configuration for Web3.py / Sepolia Testnet ---
RPC = os.getenv("WEB3_RPC")  # Alchemy/Infura Sepolia URL
PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY")  # Wallet Private Key
ACCOUNT = os.getenv("DEPLOYER_ADDRESS")  # Wallet Public Address
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")  # Custom Smart Contract Address (Optional)
CONTRACT_ABI_PATH = os.getenv("CONTRACT_ABI_PATH")  # Path to ABI file (Optional)
# ----------------------------------------------------

w3 = Web3(Web3.HTTPProvider(RPC)) if RPC else None


def create_report_hash(data: dict) -> str:
    """
    Create a deterministic SHA-256 hash of the final verification report.
    This is the 'proof hash' that we commit to the blockchain.
    """
    # sort_keys + compact separators to ensure the same structure always gives the same hash
    data_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data_str.encode()).hexdigest()


def log_verification_hash(report_hash_hex: str) -> Dict[str, Any]:
    """
    Logs the final report hash (Proof Hash) to the blockchain.
    Returns the real Ethereum Transaction Hash (Tx Hash) in the 'tx_hash' field.
    """

    # If web3 config is missing or invalid, return STUB
    if not w3 or not PRIVATE_KEY or not ACCOUNT or not w3.is_connected():
        print("BLOCKCHAIN STUB LOG:", report_hash_hex)
        return {
            "tx_hash": f"mock_tx_0x{report_hash_hex[:16]}",
            "chain_id": "STUB_TESTNET",
            "proof_hash": report_hash_hex,
        }

    # --- FIX FOR CHECKSUM ADDRESS ERROR ---
    try:
        checksum_account = Web3.to_checksum_address(ACCOUNT)
        checksum_contract = (
            Web3.to_checksum_address(CONTRACT_ADDRESS) if CONTRACT_ADDRESS else None
        )
    except Exception:
        # If addresses are invalid, log the failure locally
        return {
            "tx_hash": "mock_tx_0xerror",
            "error": "Invalid Address Configuration",
            "proof_hash": report_hash_hex,
        }
    # ------------------------------------

    # Ensure hash is correctly formatted (0x prefix)
    data_to_log = Web3.to_bytes(hexstr=report_hash_hex)

    try:
        nonce = w3.eth.get_transaction_count(checksum_account)

        # 1. Attempt Smart Contract Call (Best Practice)
        if checksum_contract and CONTRACT_ABI_PATH and os.path.exists(CONTRACT_ABI_PATH):
            with open(CONTRACT_ABI_PATH, "r") as f:
                abi = json.load(f)
            contract = w3.eth.contract(address=checksum_contract, abi=abi)

            tx = contract.functions.registerProof(data_to_log).build_transaction(
                {
                    "from": checksum_account,
                    "nonce": nonce,
                    "gas": 300000,
                    "gasPrice": w3.eth.gas_price,
                }
            )

        # 2. Fallback: Simple Data Transaction
        else:
            tx = {
                "to": checksum_account,
                "value": 0,
                "data": data_to_log,  # Embeds the Proof Hash here
                "gas": 200000,
                "gasPrice": w3.eth.gas_price,
                "nonce": nonce,
            }

        signed = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        # Use signed.rawTransaction for compatibility with older web3 versions
        tx_hash_result = w3.eth.send_raw_transaction(signed.rawTransaction).hex()

        return {
            "tx_hash": tx_hash_result,  # This is the 0x... hash
            "chain_id": w3.eth.chain_id,
            "proof_hash": report_hash_hex,  # This is the SHA-256 hash of the report
            "success": True,
        }

    except Exception as e:
        print(f"BLOCKCHAIN LIVE ERROR: {e}")
        return {
            "error": f"Live TX failed: {str(e)}",
            "tx_hash": "mock_tx_0xerror",
            "proof_hash": report_hash_hex,
        }
