"""Encriptação local Windows por DPAPI (âmbito: utilizador actual)."""
import ctypes
from ctypes import wintypes
import os

class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]


def _transform(data: bytes, encrypt: bool) -> bytes:
    if os.name != 'nt':
        raise OSError('DPAPI disponível apenas no Windows')
    if not data:
        raise ValueError('Dados vazios')
    buf = ctypes.create_string_buffer(data)
    input_blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    output_blob = _Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    proc = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    proc.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    proc.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN: sem janelas inesperadas.
    success = proc(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob))
    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    return _transform(data, True)


def unprotect(data: bytes) -> bytes:
    return _transform(data, False)
