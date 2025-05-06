# decryptor.py
from cryptography.fernet import Fernet

def load_key(key_path="secret.key"):
    with open(key_path, "rb") as f:
        return f.read()

def decrypt_password(enc_path="password.enc", key_path="secret.key"):
    key = load_key(key_path)
    cipher = Fernet(key)
    with open(enc_path, "rb") as f:
        encrypted = f.read()
    return cipher.decrypt(encrypted).decode()