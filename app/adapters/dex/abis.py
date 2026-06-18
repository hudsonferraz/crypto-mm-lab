UNISWAP_V2_PAIR_ABI = [
    {
        "name": "getReserves",
        "outputs": [
            {"type": "uint112", "name": "reserve0"},
            {"type": "uint112", "name": "reserve1"},
            {"type": "uint32", "name": "blockTimestampLast"},
        ],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "token0",
        "outputs": [{"type": "address"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    },
]

# Mainnet WETH address — used to orient reserves as base (WETH) / quote (USDC).
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
