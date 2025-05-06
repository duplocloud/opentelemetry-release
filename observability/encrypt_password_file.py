from cryptography.fernet import Fernet

# Step 1: Generate and save the key (only once)
def generate_key():
    key = Fernet.generate_key()
    with open("secret.key", "wb") as f:
        f.write(key)
    return key

# Step 2: Encrypt the file contents
def encrypt_file(file_path="password.txt", output_path="password.enc"):
    try:
        with open("secret.key", "rb") as f:
            key = f.read()
    except FileNotFoundError:
        print("No key found. Generating a new key.")
        key = generate_key()

    cipher = Fernet(key)

    with open(file_path, "rb") as f:
        data = f.read()

    encrypted = cipher.encrypt(data)

    with open(output_path, "wb") as f:
        f.write(encrypted)

    print(f"Encrypted '{file_path}' to '{output_path}'")

if __name__ == "__main__":
    encrypt_file()