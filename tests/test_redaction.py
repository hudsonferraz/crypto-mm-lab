from app.config.redaction import redact_url, rpc_provider_label


def test_redact_url_hides_key_in_path() -> None:
    assert (
        redact_url("https://eth-mainnet.g.alchemy.com/v2/SECRETKEY")
        == "https://eth-mainnet.g.alchemy.com/***"
    )


def test_redact_url_hides_query_and_credentials() -> None:
    assert (
        redact_url("https://user:pw@rpc.example.com:8545?key=abc")
        == "https://rpc.example.com:8545/***"
    )


def test_redact_url_keeps_bare_host() -> None:
    assert redact_url("https://ethereum.publicnode.com") == "https://ethereum.publicnode.com"


def test_redact_url_handles_empty_and_garbage() -> None:
    assert redact_url(None) is None
    assert redact_url("") is None
    assert redact_url("not a url") == "***"


def test_rpc_provider_label() -> None:
    assert rpc_provider_label("https://ethereum.publicnode.com") == "public"
    assert rpc_provider_label("https://mainnet.infura.io/v3/k") == "custom"
    assert rpc_provider_label(None) == "none"
