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


# --- Uniswap V3 (read-only) ---------------------------------------------------
# Function signatures and output types for the calls the V3 reader makes through
# Multicall3. Only view functions (plus QuoterV2, which is only ever eth_call'ed).

V3_SLOT0 = ("slot0()", ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"))
V3_LIQUIDITY = ("liquidity()", ("uint128",))
V3_FEE_GROWTH_GLOBAL0 = ("feeGrowthGlobal0X128()", ("uint256",))
V3_FEE_GROWTH_GLOBAL1 = ("feeGrowthGlobal1X128()", ("uint256",))
V3_TICKS = (
    "ticks(int24)",
    ("uint128", "int128", "uint256", "uint256", "int56", "uint160", "uint32", "bool"),
)
V3_TICK_BITMAP = ("tickBitmap(int16)", ("uint256",))
V3_FEE = ("fee()", ("uint24",))
V3_TICK_SPACING = ("tickSpacing()", ("int24",))
V3_TOKEN0 = ("token0()", ("address",))
V3_TOKEN1 = ("token1()", ("address",))
ERC20_DECIMALS = ("decimals()", ("uint8",))
MULTICALL3_GET_BLOCK_TIMESTAMP = ("getCurrentBlockTimestamp()", ("uint256",))
QUOTER_V2_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
QUOTER_V2_EXACT_INPUT_SINGLE = (
    "quoteExactInputSingle((address,address,uint256,uint24,uint160))",
    ("uint256", "uint160", "uint32", "uint256"),
)
