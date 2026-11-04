from app.portal.signing import build_signing_message, sign_request


def test_message_is_method_path_query_timestamp_joined_by_newlines():
    assert build_signing_message("GET", "/portal/export", "page=1", 1793799552) == "GET\n/portal/export\npage=1\n1793799552"


def test_signature_matches_the_portals_javascript():
    # Expected value computed with Node's WebCrypto (crypto.subtle HMAC SHA-256), the same API the portal's
    # browser code uses, so this cross-checks our Python against an independent implementation.
    signature = sign_request("test-secret-not-real", "GET", "/portal/export", "page=1", 1793799552)
    assert signature == "e0cb4f773fb052f9a564587ed65799f137551882f5731a0d938d8012a40bf25e"


def test_every_part_of_the_request_changes_the_signature():
    base = sign_request("s", "GET", "/portal/export", "page=1", 1)
    assert sign_request("s", "GET", "/portal/export", "page=2", 1) != base
    assert sign_request("s", "GET", "/portal/export", "page=1", 2) != base
    assert sign_request("other", "GET", "/portal/export", "page=1", 1) != base
