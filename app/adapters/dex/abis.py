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

USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
V2_GET_RESERVES = ("getReserves()", ("uint112", "uint112", "uint32"))
V2_TOKEN0 = ("token0()", ("address",))
ERC20_BALANCE_OF = ("balanceOf(address)", ("uint256",))


# --- Mempool watcher (read-only; specs/004-mempool-watcher) -----------------------
# Public Uniswap entry points whose calldata the watcher decodes (research R5).
V2_ROUTER02_ADDRESS = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
SWAP_ROUTER_ADDRESS = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
SWAP_ROUTER02_ADDRESS = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
UNIVERSAL_ROUTER_ADDRESSES = (
    "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD",
    "0x66a9893cC07D91D95644AEDD05D03f95e1dBA8Af",
    "0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B",
)

# Pool events (topic0 = keccak of the signature).
V2_SWAP_EVENT = "Swap(address,uint256,uint256,uint256,uint256,address)"
V2_SYNC_EVENT = "Sync(uint112,uint112)"
V3_SWAP_EVENT = "Swap(address,address,int256,int256,uint160,uint128,int24)"
V3_MINT_EVENT = "Mint(address,address,int24,int24,uint128,uint256,uint256)"
V3_BURN_EVENT = "Burn(address,int24,int24,uint128,uint256,uint256)"

# Read-only quotes for the upstream hops of multi-hop routes (only ever eth_call'ed).
QUOTER_V2_EXACT_INPUT = (
    "quoteExactInput(bytes,uint256)",
    ("uint256", "uint160[]", "uint32[]", "uint256"),
)
QUOTER_V2_EXACT_OUTPUT_SINGLE = (
    "quoteExactOutputSingle((address,address,uint256,uint24,uint160))",
    ("uint256", "uint160", "uint32", "uint256"),
)
V2_ROUTER_GET_AMOUNTS_OUT = ("getAmountsOut(uint256,address[])", ("uint256[]",))
