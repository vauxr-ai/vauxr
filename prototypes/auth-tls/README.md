# TLS trust prototype

The [#45 decision record](../../docs/auth/tls-server-trust.md) selects optional
TLS: default self-hosted LAN HTTP/WS requires no domain, certificate, reverse
proxy or managed service. This prototype covers only the explicitly opted-in
HTTPS/WSS trust contract. Its verified-handshake-before-credential ordering is
mandatory in that mode; configured TLS must never silently downgrade to HTTP/WS.
It does not impose TLS on LAN mode or relax auth, scopes or physical matching-code
protections. Plain LAN browser microphone capture is not guaranteed or expected
to work; the decision record provides the optional HTTPS path and #56/#57 follow-ups.

From the repository root, run `python3 prototypes/auth-tls/test_tls_trust.py`
(or `python3 test_tls_trust.py` from this directory). It creates temporary test
PKI with OpenSSL and uses `ssl.MemoryBIO` pairs; it does not bind a socket, contact
a device, or use real credentials. The tests cover authenticated hostname
success, an untrusted issuer, hostname mismatch, expiry, pre-overlap rejection,
old/new acceptance during an explicit rollover window, and old-root rejection/
new-root acceptance after retirement. No behavior or test changes are needed
for the optional-TLS decision.

This is an evidence aid, not a server implementation or a claim that stock
browsers, phones or ESP32 hardware accept a deployed setup. The root-signed leaf
fixture does not test reverse-proxy intermediate-chain delivery. It does not
test HTTP-mode auth, cookie handling, browser microphone access or production
transport gates. Failure tests do not write application data; MemoryBIO buffer
state is not used to claim whether a peer received plaintext on handshake failure.
