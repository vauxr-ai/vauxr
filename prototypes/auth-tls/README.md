# TLS trust prototype

Run `python3 test_tls_trust.py`. It creates temporary test PKI with OpenSSL and
uses `ssl.MemoryBIO` pairs; it does not bind a socket, contact a device, or use
real credentials. The tests cover authenticated hostname success, an untrusted
issuer, hostname mismatch, expiry, pre-overlap rejection, old/new acceptance
during an explicit rollover window, and old-root rejection/new-root acceptance
after retirement.

This is an evidence aid for issue #45, not a server implementation or a claim
that stock browsers, phones, or ESP32 hardware accept the proposed trust model.
Failure tests do not write application data; MemoryBIO buffer state is not used
to claim whether a peer did or did not receive plaintext.
