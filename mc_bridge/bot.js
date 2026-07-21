const express = require('express');
const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const { plugin: collectBlock } = require('mineflayer-collectblock');
const pvp = require('mineflayer-pvp').plugin;

const app = express();
app.use(express.json());

// Initialize Mineflayer Bot with comprehensive capabilities
const bot = mineflayer.createBot({
    host: 'localhost', 
    port: 25565,       
    username: 'ARIES_Bot',
    auth: 'offline'    
});

// Load all required Mineflayer plugins
bot.loadPlugin(pathfinder);
bot.loadPlugin(collectBlock);
bot.loadPlugin(pvp);

bot.once('spawn', () => {
    console.log(`ARIES Bot spawned as ${bot.username}`);
    const defaultMove = new Movements(bot);
    bot.pathfinder.setMovements(defaultMove);
});

// Listen to player chat messages in the game
bot.on('chat', async (username, message) => {
    // Ignore messages sent by the bot itself
    if (username === bot.username) return;

    console.log(`[Chat Command] <${username}> ${message}`);

    const args = message.trim().split(' ');
    const command = args[0].toLowerCase();

    try {
        if (command === 'come') {
            // Usage: come (moves to player's position)
            const targetPlayer = bot.players[username];
            if (!targetPlayer || !targetPlayer.entity) {
                bot.chat("I can't see you!");
                return;
            }
            const { x, y, z } = targetPlayer.entity.position;
            bot.chat(`Moving to your position: ${Math.round(x)}, ${Math.round(y)}, ${Math.round(z)}`);
            
            const mcData = require('minecraft-data')(bot.version);
            const defaultMove = new (require('mineflayer-pathfinder').Movements)(bot, mcData);
            bot.pathfinder.setMovements(defaultMove);
            await bot.pathfinder.goto(new (require('mineflayer-pathfinder').goals.GoalNear)(x, y, z, 2));
            bot.chat("I have arrived!");

        } else if (command === 'collect') {
            // Usage: collect oak_log 5
            const blockName = args[1] || 'oak_log';
            const count = parseInt(args[2]) || 1;
            bot.chat(`Collecting ${count} ${blockName}(s)...`);
            
            await bot.collectBlock.collect(bot.findBlock({
                matching: block => block.name === blockName,
                maxDistance: 32
            }));
            bot.chat(`Finished collecting ${blockName}!`);

        } else if (command === 'stop') {
            // Usage: stop
            bot.pathfinder.stop();
            bot.chat("Stopping current action.");
        }
    } catch (err) {
        bot.chat(`Error executing command: ${err.message}`);
    }
});

// --- MACRO ENDPOINTS ---

// 1. Move to coordinates macro
app.post('/api/move', async (req, res) => {
    const { x, y, z } = req.body;
    try {
        const goal = new goals.GoalBlock(x, y, z);
        await bot.pathfinder.goto(goal);
        res.json({ status: 'success', message: `Arrived at ${x}, ${y}, ${z}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 2. Dig / Collect specific block type macro (e.g., "oak_log", "stone")
app.post('/api/collect', async (req, res) => {
    const { blockName, count = 1 } = req.body;
    try {
        const blockType = bot.registry.blocksByName[blockName];
        if (!blockType) {
            return res.status(400).json({ status: 'error', message: `Unknown block: ${blockName}` });
        }
        
        const block = bot.findBlock({
            matching: blockType.id,
            maxDistance: 32
        });

        if (!block) {
            return res.status(404).json({ status: 'error', message: `No ${blockName} found nearby.` });
        }

        await bot.collectBlock.collect(block);
        res.json({ status: 'success', message: `Collected ${blockName}` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 3. Stop movement macro (The Interrupter)
app.delete('/api/stop', (req, res) => {
    try {
        bot.pathfinder.stop();
        bot.clearControlStates();
        bot.collectBlock.stop();
        res.json({ status: 'interrupted', message: 'All bot actions cleared.' });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});
// 4. Check Inventory macro
app.get('/api/inventory', (req, res) => {
    try {
        const items = bot.inventory.items().map(item => ({
            name: item.name,
            count: item.count
        }));
        res.json({ status: 'success', inventory: items });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 5. Place block macro (Places a block from inventory adjacent to a reference block)
app.post('/api/place', async (req, res) => {
    const { blockName, x, y, z } = req.body;
    try {
        const item = bot.inventory.items().find(i => i.name.includes(blockName));
        if (!item) {
            return res.status(400).json({ status: 'error', message: `Bot is not holding any ${blockName}` });
        }
        await bot.equip(item, 'hand');
        
        // Find the reference block at the given coordinates
        const referenceBlock = bot.blockAt(new (require('vec3'))(x, y, z));
        if (!referenceBlock) {
            return res.status(404).json({ status: 'error', message: `No reference block found at ${x}, ${y}, ${z}` });
        }

        // Place the block on top of the reference block (defaulting to top face offset {x: 0, y: 1, z: 0})
        const faceVector = new (require('vec3'))(0, 1, 0);
        await bot.placeBlock(referenceBlock, faceVector);
        
        res.json({ status: 'success', message: `Placed ${blockName} successfully` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 6. Combat / Attack nearest hostile mob macro
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
// Start the Bridge Server
const PORT = 3000;
app.listen(PORT, () => {
    console.log(`MC Bridge server running on http://localhost:${PORT}`);
});