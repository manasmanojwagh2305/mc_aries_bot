const express = require('express');
const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const { plugin: collectBlock } = require('mineflayer-collectblock');
const pvp = require('mineflayer-pvp').plugin;
const vm = require('vm');
const Vec3 = require('vec3');

// =============================================================================
// PHASE 1: PROCESS-LEVEL SAFETY NETS (C-1, H-5)
// Must be registered first, before any other code can throw.
// =============================================================================

process.on('unhandledRejection', (reason, promise) => {
    console.error('[SAFETY NET] Unhandled Promise Rejection — server surviving.');
    console.error('[SAFETY NET] Reason:', reason?.message || String(reason));
    // Intentionally NOT re-throwing. The process must survive LLM hallucinations.
});

process.on('uncaughtException', (err) => {
    console.error('[SAFETY NET] Uncaught Exception — server surviving.');
    console.error('[SAFETY NET] Error:', err.message);
    console.error('[SAFETY NET] Stack:', err.stack);
    // Intentionally NOT re-throwing. Log and continue.
});

// =============================================================================
// EXPRESS APP SETUP
// =============================================================================

const app = express();
app.use(express.json());

// PHASE 4: Global body-check middleware — catches M-3, H-2
// Returns a clear 400 if POST body is undefined (missing Content-Type header, etc.)
app.use((req, res, next) => {
    if (['POST', 'PUT', 'PATCH'].includes(req.method) && req.body === undefined) {
        return res.status(400).json({
            status: 'error',
            message: 'Request body is missing or Content-Type header is not application/json.'
        });
    }
    next();
});

// =============================================================================
// MINEFLAYER BOT INITIALIZATION
// =============================================================================

const bot = mineflayer.createBot({
    host: 'localhost',
    port: 25565,
    username: 'ARIES_Bot',
    auth: 'offline'
});

bot.loadPlugin(pathfinder);
bot.loadPlugin(collectBlock);
bot.loadPlugin(pvp);

bot.once('spawn', () => {
    console.log(`[MC Bridge] ARIES Bot spawned as ${bot.username}`);
    const defaultMove = new Movements(bot);
    bot.pathfinder.setMovements(defaultMove);
});

// =============================================================================
// PILLAR 2: CARTOGRAPHER — Passive World Map Logger
// Fires on every physics tick. Scans a 7x7x7 cube around the bot whenever it
// moves >= 8 blocks. Discovered landmark blocks are POST'd to the Python brain
// in a fire-and-forget call (no await — never blocks the game loop).
// =============================================================================

const CARTOGRAPHER_LANDMARKS = new Set([
    // Overworld ores
    'iron_ore', 'gold_ore', 'diamond_ore', 'coal_ore', 'lapis_ore',
    'emerald_ore', 'redstone_ore', 'copper_ore',
    'deepslate_iron_ore', 'deepslate_gold_ore', 'deepslate_diamond_ore',
    'deepslate_coal_ore', 'deepslate_lapis_ore', 'deepslate_emerald_ore',
    'deepslate_redstone_ore', 'deepslate_copper_ore',
    // Nether / End
    'ancient_debris', 'nether_gold_ore', 'nether_quartz_ore', 'end_portal_frame',
    // Structures
    'chest', 'trapped_chest', 'ender_chest', 'spawner', 'nether_portal',
    // Environment
    'lava', 'water',
    // Player-placed infrastructure
    'crafting_table', 'furnace', 'blast_furnace',
]);

const CARTOGRAPHER_SCAN_RADIUS = 3; // scans ±3 blocks in each axis (7x7x7 cube)
const CARTOGRAPHER_MOVE_THRESHOLD = 8; // only scan after moving this many blocks
let _lastCartographerPos = null;

bot.on('physicTick', () => {
    // Guard: bot must be fully spawned and positioned
    if (!bot.entity || !bot.entity.position) return;

    const pos = bot.entity.position;

    // Only scan if the bot has moved far enough since last scan
    if (_lastCartographerPos) {
        const moved = pos.distanceTo(_lastCartographerPos);
        if (moved < CARTOGRAPHER_MOVE_THRESHOLD) return;
    }
    _lastCartographerPos = pos.clone();

    // Scan the cube for landmark blocks
    const discovered = [];
    for (let dx = -CARTOGRAPHER_SCAN_RADIUS; dx <= CARTOGRAPHER_SCAN_RADIUS; dx++) {
        for (let dy = -CARTOGRAPHER_SCAN_RADIUS; dy <= CARTOGRAPHER_SCAN_RADIUS; dy++) {
            for (let dz = -CARTOGRAPHER_SCAN_RADIUS; dz <= CARTOGRAPHER_SCAN_RADIUS; dz++) {
                try {
                    const block = bot.blockAt(pos.offset(dx, dy, dz));
                    if (block && CARTOGRAPHER_LANDMARKS.has(block.name)) {
                        discovered.push({
                            name: block.name,
                            x:    Math.round(block.position.x),
                            y:    Math.round(block.position.y),
                            z:    Math.round(block.position.z),
                        });
                    }
                } catch (_) {
                    // blockAt can throw on unloaded chunks — silently skip
                }
            }
        }
    }

    if (discovered.length === 0) return;

    // Fire-and-forget POST to Python brain — never awaited, never blocks physics tick
    fetch('http://localhost:8000/agent/map/log', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ blocks: discovered, timestamp: Date.now() / 1000 }),
    }).catch(() => {
        // Silently discard — Python brain may be temporarily down during startup
    });
});

// =============================================================================
// ITEM ALIAS DICTIONARY & NORMALIZATION
// =============================================================================

const ITEM_ALIASES = {
    'wooden_plank': 'oak_planks',
    'wood_plank':   'oak_planks',
    'planks':       'oak_planks',
    'plank':        'oak_planks',
    'oak_plank':    'oak_planks',
    'wood':         'oak_log',
    'log':          'oak_log',
    'tree':         'oak_log',
    'stone_pick':   'stone_pickaxe',
    'wood_pickaxe': 'wooden_pickaxe',
    'wood_pick':    'wooden_pickaxe',
    'crafting_bench': 'crafting_table',
    'workbench':    'crafting_table',
    'table':        'crafting_table',
    'cobble':       'cobblestone',
    'iron':         'raw_iron',
    'iron_ore':     'raw_iron',
    'gold_ore':     'raw_gold'
};

function normalizeItemName(name) {
    if (!name || typeof name !== 'string') return name;
    const lower = name.toLowerCase().trim().replace(/ /g, '_');
    return ITEM_ALIASES[lower] || lower;
}

function findFuzzyMatches(inputName, mcData) {
    if (!mcData || !mcData.itemsByName) return [];
    const cleanInput = inputName.toLowerCase();
    return Object.keys(mcData.itemsByName)
        .filter(k => k.includes(cleanInput) || cleanInput.includes(k))
        .slice(0, 5);
}

// =============================================================================
// PHASE 4: COORDINATE VALIDATION HELPER (H-1)
// Prevents NaN/Infinity/null coordinates from entering Mineflayer functions.
// =============================================================================

function validateCoords(x, y, z) {
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
        return `Invalid coordinates: x=${x}, y=${y}, z=${z}. All values must be finite numbers, not NaN, Infinity, or null.`;
    }
    // Sanity-check world bounds (Minecraft world height: -64 to 320, XZ: ±30M)
    if (y < -64 || y > 320) {
        return `Y coordinate ${y} is outside valid Minecraft world height range (-64 to 320).`;
    }
    if (Math.abs(x) > 30_000_000 || Math.abs(z) > 30_000_000) {
        return `Coordinates (${x}, ${z}) are beyond Minecraft world boundary (±30,000,000).`;
    }
    return null; // null = valid
}

// =============================================================================
// GAME CHAT LISTENER
// PHASE 1 / H-5: Async handler wrapped in .catch() to prevent floating rejections
// =============================================================================

bot.on('chat', (username, message) => {
    // PATCH H-5: Wrap async body so unhandled rejections are captured locally
    (async () => {
        if (username === bot.username) return;

        console.log(`[Chat Command] <${username}> ${message}`);
        const args = message.trim().split(' ');
        const command = args[0].toLowerCase();

        try {
            if (command === 'come') {
                const targetPlayer = bot.players[username];
                if (!targetPlayer || !targetPlayer.entity) {
                    bot.chat("I can't see you!");
                    return;
                }
                const { x, y, z } = targetPlayer.entity.position;
                const coordErr = validateCoords(x, y, z);
                if (coordErr) { bot.chat('Invalid position data received.'); return; }

                bot.chat(`Moving to your position: ${Math.round(x)}, ${Math.round(y)}, ${Math.round(z)}`);
                const mcData = require('minecraft-data')(bot.version);
                const defaultMove = new Movements(bot, mcData);
                bot.pathfinder.setMovements(defaultMove);
                await bot.pathfinder.goto(new goals.GoalNear(x, y, z, 2));
                bot.chat('I have arrived!');

            } else if (command === 'collect') {
                const rawBlockName = args[1] || 'oak_log';
                const blockName = normalizeItemName(rawBlockName);
                bot.chat(`Collecting ${blockName}...`);
                const foundBlock = bot.findBlock({ matching: block => block.name === blockName, maxDistance: 32 });
                if (!foundBlock) { bot.chat(`No ${blockName} found nearby.`); return; }
                await bot.collectBlock.collect(foundBlock);
                bot.chat(`Finished collecting ${blockName}!`);

            } else if (command === 'fight') {
                const mobName = args[1] || 'zombie';
                const target = bot.nearestEntity(e =>
                    e.name &&
                    e.name.toLowerCase().includes(mobName.toLowerCase()) &&
                    e.position.distanceTo(bot.entity.position) < 16
                );
                if (!target) { bot.chat(`I don't see any ${mobName} nearby!`); return; }
                const weapon = bot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'));
                if (weapon) await bot.equip(weapon, 'hand');
                bot.chat(`Attacking ${target.name}!`);
                bot.pvp.attack(target);

            } else if (command === 'stop') {
                bot.pathfinder.stop();
                if (bot.pvp) bot.pvp.stop();
                bot.chat('Stopping current action.');

            } else if (command === 'aries' || command === 'ai') {
                const prompt = args.slice(1).join(' ');
                if (!prompt) { bot.chat('What do you want me to do?'); return; }
                bot.chat('Thinking...');
                try {
                    const response = await fetch('http://localhost:8000/agent/chat', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ prompt })
                    });
                    const data = await response.json();
                    bot.chat(data.status === 'success' ? data.response : 'I got confused processing that.');
                } catch (err) {
                    bot.chat(`Brain connection failed: ${err.message}`);
                }
            }
        } catch (err) {
            bot.chat(`Error executing command: ${err.message}`);
        }
    })().catch(err => {
        // H-5: Last-resort catch for any floating async rejection from this handler
        console.error('[Chat Handler Floating Rejection]', err.message);
        try { bot.chat(`Caught internal error: ${err.message.slice(0, 80)}`); } catch (_) {}
    });
});

// =============================================================================
// TELEMETRY ENDPOINT
// Voyager blueprint: nearbyEntities sorted nearest→farthest; underground biome override.
// =============================================================================

// Surface block set used for Voyager underground biome detection
const SURFACE_BLOCK_NAMES = new Set(['dirt', 'grass_block', 'sand', 'gravel', 'snow', 'mycelium']);
const LOG_BLOCK_NAMES = new Set(['oak_log','birch_log','spruce_log','jungle_log','acacia_log','dark_oak_log','mangrove_log','cherry_log']);

app.get('/api/telemetry', (req, res) => {
    try {
        if (!bot || !bot.entity) {
            return res.status(503).json({ status: 'error', message: 'Bot is initializing or not yet spawned.' });
        }

        const pos = bot.entity.position;
        const inventory = bot.inventory.items().map(item => ({
            name: item.name, count: item.count, slot: item.slot
        }));
        const inventoryUsed = bot.inventory.items().length;

        const heldItem    = bot.heldItem ? bot.heldItem.name : null;
        const offHandItem = bot.inventory.slots[45] ? bot.inventory.slots[45].name : null;
        const armor = {
            helmet:     bot.inventory.slots[5]  ? bot.inventory.slots[5].name  : null,
            chestplate: bot.inventory.slots[6]  ? bot.inventory.slots[6].name  : null,
            leggings:   bot.inventory.slots[7]  ? bot.inventory.slots[7].name  : null,
            boots:      bot.inventory.slots[8]  ? bot.inventory.slots[8].name  : null
        };

        // ── Biome detection with Voyager underground override ────────────────
        // Voyager: if none of dirt|log|grass|sand|snow in nearby voxels → 'underground'
        let biomeName = 'unknown';
        let voxelNames = [];
        try {
            const blockAtBot = bot.blockAt(pos);
            if (blockAtBot && blockAtBot.biome) biomeName = blockAtBot.biome.name || 'unknown';

            // Scan a small 3x3x3 voxel cube for surface block presence
            for (let dx = -1; dx <= 1; dx++) {
                for (let dy = -1; dy <= 1; dy++) {
                    for (let dz = -1; dz <= 1; dz++) {
                        try {
                            const b = bot.blockAt(pos.offset(dx, dy, dz));
                            if (b && b.name !== 'air') voxelNames.push(b.name);
                        } catch (_) {}
                    }
                }
            }

            // Voyager design invariant: no surface or log blocks visible → underground
            const hasSurfaceBlock = voxelNames.some(n =>
                SURFACE_BLOCK_NAMES.has(n) || LOG_BLOCK_NAMES.has(n)
            );
            if (!hasSurfaceBlock) biomeName = 'underground';

        } catch (_) {}

        // ── Voyager: nearbyEntities sorted ascending by distance (nearest to farthest) ──
        const nearbyEntities = Object.values(bot.entities)
            .filter(e => e && e.position && e !== bot.entity && e.position.distanceTo(pos) <= 24)
            .map(e => ({
                name:     e.name || e.username || 'unknown',
                type:     e.type,
                distance: Math.round(e.position.distanceTo(pos) * 10) / 10,
                position: { x: Math.round(e.position.x), y: Math.round(e.position.y), z: Math.round(e.position.z) }
            }))
            .sort((a, b) => a.distance - b.distance);  // Voyager: nearest to farthest

        res.json({
            status: 'success',
            telemetry: {
                health:       bot.health,
                food:         bot.food,
                saturation:   bot.foodSaturation,
                position: {
                    x: Math.round(pos.x * 10) / 10,
                    y: Math.round(pos.y * 10) / 10,
                    z: Math.round(pos.z * 10) / 10
                },
                biome:        biomeName,             // may be 'underground' via override
                timeOfDay:    bot.time ? bot.time.timeOfDay : 0,
                isRaining:    bot.isRaining || false,
                inventory,
                inventoryUsed,                       // Voyager overflow guard needs this
                voxels:       [...new Set(voxelNames)].slice(0, 24),
                equipped:     { hand: heldItem, offhand: offHandItem, armor },
                gameMode:     bot.game ? bot.game.gameMode : 'survival',
                nearbyEntities: nearbyEntities.slice(0, 20)  // already distance-sorted
            }
        });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// =============================================================================
// RESILIENT CRAFTING ENDPOINT
// =============================================================================

app.post('/api/craft', async (req, res) => {
    const { itemName: rawItemName, count = 1 } = req.body || {};
    try {
        if (!rawItemName || typeof rawItemName !== 'string') {
            return res.status(400).json({ status: 'error', message: 'itemName must be a non-empty string.' });
        }
        if (!Number.isInteger(count) || count < 1 || count > 64) {
            return res.status(400).json({ status: 'error', message: 'count must be an integer between 1 and 64.' });
        }

        const itemName = normalizeItemName(rawItemName);
        const mcData = require('minecraft-data')(bot.version);
        const itemRecipe = mcData.itemsByName[itemName];

        if (!itemRecipe) {
            const suggestions = findFuzzyMatches(rawItemName, mcData);
            const suggText = suggestions.length > 0 ? ` Did you mean: ${suggestions.join(', ')}?` : '';
            return res.status(400).json({
                status: 'error',
                message: `Unknown Minecraft item '${rawItemName}'.${suggText}`
            });
        }

        const recipes = bot.recipesFor(itemRecipe.id, null, 1, false);
        if (!recipes || recipes.length === 0) {
            return res.status(400).json({
                status: 'error',
                message: `No recipe available to craft ${itemName}. Missing required ingredients in inventory.`
            });
        }

        const recipe = recipes[0];
        let craftingTable = null;

        if (recipe.requiresTable) {
            craftingTable = bot.findBlock({ matching: mcData.blocksByName.crafting_table.id, maxDistance: 4 });

            if (!craftingTable) {
                const distantTable = bot.findBlock({ matching: mcData.blocksByName.crafting_table.id, maxDistance: 32 });
                if (distantTable) {
                    try {
                        const defaultMove = new Movements(bot, mcData);
                        bot.pathfinder.setMovements(defaultMove);
                        await bot.pathfinder.goto(new goals.GoalNear(
                            distantTable.position.x, distantTable.position.y, distantTable.position.z, 2
                        ));
                        craftingTable = distantTable;
                    } catch (e) {
                        return res.status(400).json({
                            status: 'error',
                            message: `Crafting ${itemName} requires a crafting table — pathfinder failed to reach it: ${e.message}`
                        });
                    }
                } else {
                    return res.status(400).json({
                        status: 'error',
                        message: `Crafting ${itemName} requires a 3x3 crafting table, but none was found within 32 blocks.`
                    });
                }
            }
        }

        await bot.craft(recipe, count, craftingTable);
        res.json({ status: 'success', message: `Successfully crafted ${count} ${itemName}(s).` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// =============================================================================
// PHASE 3: HARDENED DYNAMIC SCRIPT EXECUTION ENDPOINT (C-1, C-2, C-3, H-3, M-4)
// =============================================================================

app.post('/api/execute-script', async (req, res) => {
    const { code, timeoutMs = 45000 } = req.body || {};

    // M-4: Length guard
    if (!code || typeof code !== 'string') {
        return res.status(400).json({ status: 'error', message: 'No valid JavaScript code string provided.' });
    }
    if (code.length > 50000) {
        return res.status(400).json({ status: 'error', message: 'Code payload exceeds 50KB safety limit.' });
    }
    // Validate timeoutMs bounds
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1000 || timeoutMs > 120000) {
        return res.status(400).json({ status: 'error', message: 'timeoutMs must be a number between 1000 and 120000.' });
    }

    const logs = [];

    // H-3: Track all timer handles created inside sandbox so we can kill them after execution
    const activeTimers = new Set();

    const customConsole = {
        log:  (...args) => logs.push(args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')),
        error:(...args) => logs.push('[ERROR] ' + args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')),
        warn: (...args) => logs.push('[WARN] '  + args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' '))
    };

    // H-3: Wrapped timer factories that track handles for post-execution cleanup
    const trackedSetTimeout = (fn, ms, ...args) => {
        const id = setTimeout(fn, ms, ...args);
        activeTimers.add({ type: 'timeout', id });
        return id;
    };
    const trackedClearTimeout = (id) => { clearTimeout(id); };
    const trackedSetInterval = (fn, ms, ...args) => {
        const id = setInterval(fn, ms, ...args);
        activeTimers.add({ type: 'interval', id });
        return id;
    };
    const trackedClearInterval = (id) => { clearInterval(id); };

    // H-3: Cleanup — kill every timer the sandbox spawned
    const cleanupTimers = () => {
        for (const { type, id } of activeTimers) {
            try {
                if (type === 'interval') clearInterval(id);
                else clearTimeout(id);
            } catch (_) {}
        }
        activeTimers.clear();
    };

    let mcData;
    try { mcData = require('minecraft-data')(bot.version); } catch (_) { mcData = null; }

    // C-2: Whitelist-only proxy — blocks access to bot._client, emit, socket, removeAllListeners
    const BLOCKED_BOT_PROPS = new Set(['_client', '_events', '_eventsCount', 'emit', 'removeAllListeners', 'removeListener', 'on', 'once', 'off', 'prependListener', 'socket', 'rawListeners']);
    const safeBotProxy = new Proxy(bot, {
        get(target, prop) {
            if (BLOCKED_BOT_PROPS.has(String(prop))) {
                throw new Error(`Sandbox: Access to bot.${String(prop)} is not permitted for security reasons.`);
            }
            const val = target[prop];
            return typeof val === 'function' ? val.bind(target) : val;
        },
        set(target, prop, value) {
            if (BLOCKED_BOT_PROPS.has(String(prop))) {
                throw new Error(`Sandbox: Setting bot.${String(prop)} is not permitted.`);
            }
            target[prop] = value;
            return true;
        }
    });

    // ==========================================================================
    // VOYAGER CONTROL PRIMITIVES — injected into every sandbox execution
    // These wrap raw Mineflayer APIs with pathfinding, error recovery, and
    // inventory pre-checks. Generated code MUST call these instead of bot.dig,
    // bot.craft, bot.openFurnace, bot.placeBlock, or bot.attack directly.
    // ==========================================================================

    /**
     * mineBlock(bot, name, count) — find and collect `count` blocks of `name`.
     * Uses collectBlock plugin for safe gathering with pathfinding.
     */
    async function mineBlock(proxyBot, name, count = 1) {
        const mcD = require('minecraft-data')(proxyBot.version);
        const blockType = mcD.blocksByName[name];
        if (!blockType) throw new Error(`mineBlock: unknown block '${name}'`);
        let collected = 0;
        while (collected < count) {
            const block = proxyBot.findBlock({ matching: blockType.id, maxDistance: 32 });
            if (!block) throw new Error(`mineBlock: no '${name}' found within 32 blocks`);
            await proxyBot.collectBlock.collect(block);
            collected++;
        }
        proxyBot.chat(`Mined ${count} ${name}.`);
    }

    /**
     * craftItem(bot, name, count) — craft `count` of item `name`.
     * Automatically paths to a crafting table if the recipe requires one.
     */
    async function craftItem(proxyBot, name, count = 1) {
        const mcD = require('minecraft-data')(proxyBot.version);
        const itemDef = mcD.itemsByName[name];
        if (!itemDef) throw new Error(`craftItem: unknown item '${name}'`);
        const recipes = proxyBot.recipesFor(itemDef.id, null, 1, false);
        if (!recipes || recipes.length === 0)
            throw new Error(`craftItem: no recipe for '${name}' with current inventory`);
        const recipe = recipes[0];
        let craftingTable = null;
        if (recipe.requiresTable) {
            craftingTable = proxyBot.findBlock({ matching: mcD.blocksByName.crafting_table.id, maxDistance: 4 });
            if (!craftingTable) {
                const far = proxyBot.findBlock({ matching: mcD.blocksByName.crafting_table.id, maxDistance: 32 });
                if (!far) throw new Error(`craftItem: '${name}' needs a crafting table but none found within 32 blocks`);
                await proxyBot.pathfinder.goto(new Vec3(far.position.x, far.position.y, far.position.z));
                craftingTable = far;
            }
        }
        await proxyBot.craft(recipe, count, craftingTable);
        proxyBot.chat(`Crafted ${count} ${name}.`);
    }

    /**
     * smeltItem(bot, name, count) — smelt `count` of `name` in the nearest furnace.
     */
    async function smeltItem(proxyBot, name, count = 1) {
        const mcD = require('minecraft-data')(proxyBot.version);
        const furnaceBlock = proxyBot.findBlock({
            matching: [mcD.blocksByName.furnace?.id, mcD.blocksByName.blast_furnace?.id].filter(Boolean),
            maxDistance: 32
        });
        if (!furnaceBlock) throw new Error(`smeltItem: no furnace found within 32 blocks`);
        await proxyBot.pathfinder.goto(new Vec3(
            furnaceBlock.position.x, furnaceBlock.position.y, furnaceBlock.position.z
        ));
        const furnace = await proxyBot.openFurnace(furnaceBlock);
        const inputItem = proxyBot.inventory.items().find(i => i.name.includes(name));
        if (!inputItem) { await furnace.close(); throw new Error(`smeltItem: '${name}' not in inventory`); }
        await furnace.putInput(inputItem.type, null, count);
        // Wait for smelting (roughly 10s per item)
        await new Promise(r => setTimeout(r, count * 10000 + 2000));
        const result = furnace.outputItem();
        if (result) await furnace.takeOutput();
        await furnace.close();
        proxyBot.chat(`Smelted ${count} ${name}.`);
    }

    /**
     * placeItem(bot, name, position) — place block `name` at Vec3 position.
     */
    async function placeItem(proxyBot, name, position) {
        const item = proxyBot.inventory.items().find(i => i.name === name || i.name.includes(name));
        if (!item) throw new Error(`placeItem: '${name}' not in inventory`);
        await proxyBot.equip(item, 'hand');
        const refBlock = proxyBot.blockAt(position.offset(0, -1, 0));
        if (!refBlock) throw new Error(`placeItem: no reference block below ${JSON.stringify(position)}`);
        await proxyBot.placeBlock(refBlock, new Vec3(0, 1, 0));
        proxyBot.chat(`Placed ${name}.`);
    }

    /**
     * killMob(bot, name, timeout) — locate and kill the nearest mob of `name`.
     */
    async function killMob(proxyBot, name, timeout = 30000) {
        const target = proxyBot.nearestEntity(e =>
            e && e.name && e.name.toLowerCase().includes(name.toLowerCase()) &&
            e.position.distanceTo(proxyBot.entity.position) < 24
        );
        if (!target) throw new Error(`killMob: no '${name}' found nearby`);
        const weapon = proxyBot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'));
        if (weapon) await proxyBot.equip(weapon, 'hand');
        proxyBot.pvp.attack(target);
        proxyBot.chat(`Attacking ${name}!`);
        // Wait for mob death or timeout
        await new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error(`killMob: timeout killing ${name}`)), timeout);
            const check = setInterval(() => {
                if (!target.isValid) { clearInterval(check); clearTimeout(timer); resolve(); }
            }, 500);
        });
    }

    /**
     * exploreUntil(bot, direction, maxDistance, callback)
     * Move in direction [dx,dy,dz] until callback() returns truthy or maxDistance blocks walked.
     * Voyager blueprint: always call this when you cannot find a block nearby.
     */
    async function exploreUntil(proxyBot, direction, maxDistance, callback) {
        const [dx, dy, dz] = direction;
        const start = proxyBot.entity.position.clone();
        let walked = 0;
        const STEP = 16;
        while (walked < maxDistance) {
            const target = proxyBot.entity.position.offset(
                dx * STEP, dy * STEP, dz * STEP
            );
            try {
                await proxyBot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 2));
            } catch (_) { /* pathfinder may fail on terrain — keep going */ }
            walked = proxyBot.entity.position.distanceTo(start);
            if (callback && callback()) break;
        }
        proxyBot.chat(`Explored ${Math.round(walked)} blocks.`);
    }

    // C-2, C-3: Minimal, explicit sandbox — no fetch, no Object/Array/String/Number/Boolean constructors
    // (Primitive literals like [], {}, '', 0, true still work inside vm without exposing host constructors)
    const sandbox = {
        bot:              safeBotProxy,   // C-2: whitelisted proxy, not raw bot
        Vec3,
        pathfinder,
        goals,
        Movements,
        mcData,
        normalizeItemName,
        // Voyager control primitives — always available, always preferred over raw Mineflayer API
        mineBlock:        (name, count)         => mineBlock(safeBotProxy, name, count),
        craftItem:        (name, count)         => craftItem(safeBotProxy, name, count),
        smeltItem:        (name, count)         => smeltItem(safeBotProxy, name, count),
        placeItem:        (name, pos)           => placeItem(safeBotProxy, name, pos),
        killMob:          (name, timeout)       => killMob(safeBotProxy, name, timeout),
        exploreUntil:     (dir, maxD, cb)      => exploreUntil(safeBotProxy, dir, maxD, cb),
        console:          customConsole,
        setTimeout:       trackedSetTimeout,   // H-3: tracked
        clearTimeout:     trackedClearTimeout,
        setInterval:      trackedSetInterval,  // H-3: tracked
        clearInterval:    trackedClearInterval,
        Promise,
        Math,
        Date,
        JSON,
        // NOTE: fetch intentionally excluded (C-3) — LLM cannot make arbitrary HTTP calls
        // NOTE: Object/Array/String/Number/Boolean intentionally excluded (C-2) — prevents prototype pollution
        require: (moduleName) => {
            const ALLOWED = {
                'vec3':                 Vec3,
                'minecraft-data':       require('minecraft-data'),
                'mineflayer-pathfinder':require('mineflayer-pathfinder')
            };
            if (ALLOWED[moduleName] !== undefined) return ALLOWED[moduleName];
            throw new Error(`Sandbox: require('${moduleName}') is not permitted.`);
        }
    };

    // C-2: Freeze sandbox before passing to vm — prevents prototype pollution from within
    Object.freeze(sandbox);
    const context = vm.createContext(sandbox);

    // C-1: Async IIFE with internal error capture — returns structured result object
    const wrappedCode = `
(async () => {
    try {
        ${code}
        return { _success: true };
    } catch (err) {
        return { _success: false, _error: err.message, _stack: err.stack };
    }
})();
`;

    let vmPromise;
    try {
        const script = new vm.Script(wrappedCode, { filename: 'dynamic_script.js' });

        // C-1: The vm { timeout } option catches synchronous infinite loops.
        // For async code, the Promise.race below handles the wall-clock timeout.
        vmPromise = script.runInContext(context, { timeout: 8000 }); // 8s sync limit

        // C-1: CRITICAL — attach .catch() immediately to suppress dangling rejections
        // that fire AFTER our Promise.race resolves. Without this, Node crashes.
        if (vmPromise && typeof vmPromise.catch === 'function') {
            vmPromise.catch((err) => {
                console.error('[Sandbox Dangling Rejection suppressed]', err?.message || err);
            });
        }
    } catch (syncErr) {
        // Synchronous compile error or synchronous timeout (vm { timeout } fired)
        cleanupTimers();
        return res.status(500).json({
            status: 'error',
            message: `Synchronous execution error: ${syncErr.message}`,
            stack: syncErr.stack,
            logs: logs.join('\n')
        });
    }

    try {
        // C-1: Wall-clock timeout races against the async vm promise
        const wallClockTimeout = new Promise((_, reject) =>
            setTimeout(() => reject(new Error(`Script timed out after ${timeoutMs}ms`)), timeoutMs)
        );

        const result = await Promise.race([vmPromise, wallClockTimeout]);

        cleanupTimers();

        // C-1: Handle structured internal error returned by the async IIFE catch block
        if (result && result._success === false) {
            return res.status(400).json({
                status: 'error',
                message: result._error || 'Script returned an internal error.',
                stack: result._stack,
                logs: logs.join('\n')
            });
        }

        res.json({
            status: 'success',
            result: 'Script completed successfully',
            logs: logs.join('\n')
        });

    } catch (err) {
        cleanupTimers();
        res.status(500).json({
            status: 'error',
            message: err.message,
            stack: err.stack,
            logs: logs.join('\n')
        });
    }
});

// =============================================================================
// EXPANDED ATOMIC ACTION ENDPOINTS (with coord validation applied)
// =============================================================================

app.post('/api/use-item', async (req, res) => {
    const { hand = 'hand', action = 'activate', target } = req.body || {};
    try {
        const offHand = hand === 'off-hand';

        if (action === 'consume') {
            await bot.consume();
            return res.json({ status: 'success', message: 'Item consumed/eaten.' });

        } else if (action === 'useOnBlock' && target) {
            // H-1: Validate target coords before Vec3 construction
            const coordErr = validateCoords(target.x, target.y, target.z);
            if (coordErr) return res.status(400).json({ status: 'error', message: coordErr });

            const refBlock = bot.blockAt(new Vec3(target.x, target.y, target.z));
            if (!refBlock) {
                return res.status(404).json({ status: 'error', message: `Block at ${target.x}, ${target.y}, ${target.z} not found.` });
            }
            await bot.activateBlock(refBlock, new Vec3(0, 1, 0));
            return res.json({ status: 'success', message: `Used item on block at ${target.x}, ${target.y}, ${target.z}` });

        } else if (action === 'deactivate') {
            bot.deactivateItem();
            return res.json({ status: 'success', message: 'Deactivated item.' });

        } else {
            await bot.activateItem(offHand);
            return res.json({ status: 'success', message: `Activated item in ${hand}.` });
        }
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/interact-block', async (req, res) => {
    const { x, y, z, action = 'right-click' } = req.body || {};

    // H-1: Validate coordinates
    const coordErr = validateCoords(x, y, z);
    if (coordErr) return res.status(400).json({ status: 'error', message: coordErr });

    try {
        const targetBlock = bot.blockAt(new Vec3(x, y, z));
        if (!targetBlock) {
            return res.status(404).json({ status: 'error', message: `No block found at ${x}, ${y}, ${z}` });
        }

        if (action === 'open') {
            const container = await bot.openContainer(targetBlock);
            const items = container.containerItems().map(i => ({ name: i.name, count: i.count }));
            await container.close();
            return res.json({ status: 'success', message: `Opened ${targetBlock.name}`, items });
        } else if (action === 'sleep') {
            await bot.sleep(targetBlock);
            return res.json({ status: 'success', message: 'Bot went to sleep in bed.' });
        } else {
            await bot.activateBlock(targetBlock);
            return res.json({ status: 'success', message: `Interacted with ${targetBlock.name}` });
        }
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/inventory-manage', async (req, res) => {
    const { action = 'equip', itemName: rawItemName, destination = 'hand', fromSlot, toSlot } = req.body || {};
    try {
        const itemName = normalizeItemName(rawItemName);

        if (action === 'move') {
            if (!Number.isInteger(fromSlot) || !Number.isInteger(toSlot) || fromSlot < 0 || toSlot < 0) {
                return res.status(400).json({ status: 'error', message: 'fromSlot and toSlot must be non-negative integers.' });
            }
            await bot.moveSlotItem(fromSlot, toSlot);
            return res.json({ status: 'success', message: `Moved item from slot ${fromSlot} to ${toSlot}` });

        } else if (action === 'unequip') {
            await bot.unequip(destination);
            return res.json({ status: 'success', message: `Unequipped ${destination}` });

        } else {
            if (!itemName) return res.status(400).json({ status: 'error', message: 'itemName is required for equip action.' });
            const item = bot.inventory.items().find(i => i.name.includes(itemName));
            if (!item) {
                return res.status(404).json({ status: 'error', message: `Item '${rawItemName}' (normalized: ${itemName}) not in inventory.` });
            }
            await bot.equip(item, destination);
            return res.json({ status: 'success', message: `Equipped ${item.name} to ${destination}` });
        }
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// =============================================================================
// MACRO ENDPOINTS (all with coord validation and body guards)
// =============================================================================

app.post('/api/move', async (req, res) => {
    const { x, y, z } = req.body || {};
    // H-1: Coordinate validation
    const coordErr = validateCoords(x, y, z);
    if (coordErr) return res.status(400).json({ status: 'error', message: coordErr });
    try {
        await bot.pathfinder.goto(new goals.GoalBlock(x, y, z));
        res.json({ status: 'success', message: `Arrived at ${x}, ${y}, ${z}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/collect', async (req, res) => {
    const { blockName: rawBlockName, count = 1 } = req.body || {};
    try {
        if (!rawBlockName) return res.status(400).json({ status: 'error', message: 'blockName is required.' });
        const blockName = normalizeItemName(rawBlockName);
        const blockType = bot.registry.blocksByName[blockName];
        if (!blockType) {
            return res.status(400).json({ status: 'error', message: `Unknown block: '${rawBlockName}' (normalized: '${blockName}')` });
        }
        const block = bot.findBlock({ matching: blockType.id, maxDistance: 32 });
        if (!block) {
            return res.status(404).json({ status: 'error', message: `No ${blockName} found nearby within 32 blocks.` });
        }
        await bot.collectBlock.collect(block);
        res.json({ status: 'success', message: `Collected ${blockName}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.delete('/api/stop', (req, res) => {
    try {
        bot.pathfinder.stop();
        if (bot.pvp) bot.pvp.stop();
        bot.clearControlStates();
        bot.collectBlock.stop();
        res.json({ status: 'interrupted', message: 'All bot actions cleared.' });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.get('/api/inventory', (req, res) => {
    try {
        const items = bot.inventory.items().map(item => ({ name: item.name, count: item.count }));
        res.json({ status: 'success', inventory: items });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/place', async (req, res) => {
    const { blockName: rawBlockName, x, y, z } = req.body || {};
    // H-1: Coordinate validation
    const coordErr = validateCoords(x, y, z);
    if (coordErr) return res.status(400).json({ status: 'error', message: coordErr });
    try {
        const blockName = normalizeItemName(rawBlockName);
        const item = bot.inventory.items().find(i => i.name.includes(blockName));
        if (!item) {
            return res.status(400).json({ status: 'error', message: `No ${blockName} in inventory to place.` });
        }
        await bot.equip(item, 'hand');
        const referenceBlock = bot.blockAt(new Vec3(x, y, z));
        if (!referenceBlock) {
            return res.status(404).json({ status: 'error', message: `No reference block found at ${x}, ${y}, ${z}` });
        }
        await bot.placeBlock(referenceBlock, new Vec3(0, 1, 0));
        res.json({ status: 'success', message: `Placed ${blockName} successfully` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/pvp', (req, res) => {
    try {
        const filter = entity => entity.type === 'mob' && entity.mobType !== 'ArmorStand';
        const target = bot.nearestEntity(filter);
        if (!target) {
            return res.status(404).json({ status: 'error', message: 'No target entities found nearby.' });
        }
        bot.pvp.attack(target);
        res.json({ status: 'success', message: `Attacking ${target.name || target.username}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/deposit', async (req, res) => {
    const { itemName: rawItemName, count } = req.body || {};
    try {
        if (!rawItemName) return res.status(400).json({ status: 'error', message: 'itemName is required.' });
        const itemName = normalizeItemName(rawItemName);
        const chestToOpen = bot.findBlock({ matching: bot.registry.blocksByName.chest.id, maxDistance: 6 });
        if (!chestToOpen) {
            return res.status(404).json({ status: 'error', message: 'No chest found within 6 blocks.' });
        }
        const itemToDeposit = bot.inventory.items().find(i => i.name === itemName);
        if (!itemToDeposit) {
            return res.status(400).json({ status: 'error', message: `Bot does not have any ${itemName}.` });
        }
        const depositCount = count || itemToDeposit.count;
        const chest = await bot.openContainer(chestToOpen);
        await chest.deposit(itemToDeposit.type, null, depositCount);
        await chest.close();
        res.json({ status: 'success', message: `Deposited ${depositCount} ${itemName} into the chest.` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/fight', async (req, res) => {
    const { mobName } = req.body || {};
    try {
        if (!mobName || typeof mobName !== 'string') {
            return res.status(400).json({ status: 'error', message: 'mobName must be a non-empty string.' });
        }
        const targetMob = bot.nearestEntity(entity => {
            if (!entity || !entity.position) return false;
            if (entity.position.distanceTo(bot.entity.position) > 20) return false;
            const nameMatch = entity.name && entity.name.toLowerCase().includes(mobName.toLowerCase());
            const typeMatch = entity.type === 'mob' && mobName.toLowerCase() === 'mob';
            return nameMatch || typeMatch;
        });
        if (!targetMob) {
            return res.status(404).json({ status: 'error', message: `No nearby ${mobName} found within 20 blocks.` });
        }
        const weapon = bot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'));
        if (weapon) await bot.equip(weapon, 'hand');
        bot.pvp.attack(targetMob);
        res.json({ status: 'success', message: `Engaged combat with ${targetMob.name || mobName}!` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.get('/api/surroundings', (req, res) => {
    try {
        const botPos = bot.entity.position;
        const blocks = [];
        for (let x = -5; x <= 5; x++) {
            for (let y = -2; y <= 2; y++) {
                for (let z = -5; z <= 5; z++) {
                    const block = bot.blockAt(botPos.offset(x, y, z));
                    if (block && block.name !== 'air') blocks.push(block.name);
                }
            }
        }
        const entities = Object.values(bot.entities)
            .filter(e => e.position && e.position.distanceTo(botPos) < 16 && e !== bot.entity)
            .map(e => e.name || e.username || 'unknown');

        res.json({
            status: 'success',
            position: { x: Math.round(botPos.x), y: Math.round(botPos.y), z: Math.round(botPos.z) },
            nearby_blocks: [...new Set(blocks)].slice(0, 15),
            nearby_entities: [...new Set(entities)]
        });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

app.post('/api/break-at', async (req, res) => {
    const { x, y, z } = req.body || {};
    // H-1: Coordinate validation
    const coordErr = validateCoords(x, y, z);
    if (coordErr) return res.status(400).json({ status: 'error', message: coordErr });
    try {
        const targetBlock = bot.blockAt(new Vec3(x, y, z));
        if (!targetBlock || targetBlock.name === 'air') {
            return res.status(404).json({ status: 'error', message: 'No valid block found at those coordinates.' });
        }
        await bot.pathfinder.goto(new goals.GoalNear(x, y, z, 2));
        await bot.dig(targetBlock);
        res.json({ status: 'success', message: `Successfully broke ${targetBlock.name} at ${x}, ${y}, ${z}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// =============================================================================
// START BRIDGE SERVER
// =============================================================================

const PORT = 3000;
app.listen(PORT, () => {
    console.log(`[MC Bridge] Hardened server listening on http://localhost:${PORT}`);
});