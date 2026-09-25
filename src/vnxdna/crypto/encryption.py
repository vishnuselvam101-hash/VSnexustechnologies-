import os
try:
 from cryptography.fernet import Fernet, InvalidToken
except ImportError:
 Fernet = None
 class InvalidToken(Exception): pass
def _require():
 if Fernet is None: raise RuntimeError('Encryption requires the cryptography package.')
def generate_key()->str:
 _require(); return Fernet.generate_key().decode()
def encrypt(data:bytes,key:str)->bytes:
 _require(); return Fernet(key.encode()).encrypt(data)
def decrypt(data:bytes,key:str)->bytes:
 _require()
 try:return Fernet(key.encode()).decrypt(data)
 except InvalidToken as e: raise ValueError("Authenticated decryption failed.") from e
def key_from_environment()->str|None:return os.getenv("VNXDNA_KEY")
