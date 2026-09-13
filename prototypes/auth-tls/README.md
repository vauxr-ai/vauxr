# TLS trust prototype

Run `python3 test_tls_trust.py`. It creates temporary test PKI with OpenSSL and
uses `ssl.MemoryBIO` pairs; it does not bind a socket, contact a device, or use
real credentials. The tests cover authenticated hostname success, an untrusted
issuer, hostname mismatch, expiry, and an explicit two-root rollover window.

This is an evidence aid for issue #45, not a server implementation or a claim
that stock browsers, phones, or ESP32 hardware accept the proposed trust model.
