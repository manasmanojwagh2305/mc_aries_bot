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
        } else if (command === 'fight') {
            // Usage: fight zombie
            const mobName = args[1] || 'zombie';
            const target = bot.nearestEntity(e => e.name && e.name.toLowerCase().includes(mobName.toLowerCase()) && e.position.distanceTo(bot.entity.position) < 16);
            
            if (!target) {
                bot.chat(`I don't see any ${mobName} nearby!`);
                return;
            }

            const weapon = bot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'));
            if (weapon) await bot.equip(weapon, 'hand');

            bot.chat(`Attacking ${target.name}!`);
            bot.pvp.attack(target);
        
        } else if (command === 'stop') {
            // Usage: stop
            bot.pathfinder.stop();
            if (bot.pvp) {
                bot.pvp.stop();
            }
            bot.chat("Stopping current action.");
            
        } else if (command === 'aries' || command === 'ai') {
            // Usage: aries [natural language prompt]
            const prompt = args.slice(1).join(' ');
            if (!prompt) {
                bot.chat("What do you want me to do?");
                return;
            }
            
            bot.chat("Thinking...");
            
            try {
                // Forward the prompt to the Python FastAPI Brain
                const response = await fetch('http://localhost:8000/agent/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt: prompt })
                });
                
                const data = await response.json();
                
                if (data.status === 'success') {
                    // Bot speaks the LLM's response in the game chat
                    bot.chat(data.response);
                } else {
                    bot.chat("I got confused processing that.");
                }
            } catch (err) {
                bot.chat(`Brain connection failed: ${err.message}`);
            }
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
        if (bot.pvp) {
            bot.pvp.stop();
        }
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

// 7. Deposit items into a nearby chest
app.post('/api/deposit', async (req, res) => {
    const { itemName, count } = req.body;
    try {
        // Find the nearest chest block
        const chestToOpen = bot.findBlock({
            matching: bot.registry.blocksByName.chest.id,
            maxDistance: 6
        });

        if (!chestToOpen) {
            return res.status(404).json({ status: 'error', message: 'No chest found nearby.' });
        }

        // Check if the bot actually has the item
        const itemToDeposit = bot.inventory.items().find(i => i.name === itemName);
        if (!itemToDeposit) {
            return res.status(400).json({ status: 'error', message: `Bot does not have any ${itemName}.` });
        }

        const depositCount = count || itemToDeposit.count;

        // Open chest, deposit, and close
        const chest = await bot.openContainer(chestToOpen);
        await chest.deposit(itemToDeposit.type, null, depositCount);
        await chest.close();

        res.json({ status: 'success', message: `Deposited ${depositCount} ${itemName} into the chest.` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 8. Craft items (e.g. oak_planks, crafting_table, sticks)
app.post('/api/craft', async (req, res) => {
    const { itemName, count } = req.body;
    try {
        const mcData = require('minecraft-data')(bot.version);
        const itemRecipe = mcData.itemsByName[itemName];
        
        if (!itemRecipe) {
            return res.status(400).json({ status: 'error', message: `Unknown item: ${itemName}` });
        }

        const recipes = bot.recipesFor(itemRecipe.id, null, 1, false);
        if (!recipes || recipes.length === 0) {
            return res.status(400).json({ status: 'error', message: `No recipe available for ${itemName} with current inventory.` });
        }

        const recipe = recipes[0];
        
        // Check if a crafting table is required (recipes requiring 3x3 grid)
        let craftingTable = null;
        if (recipe.requiresTable) {
            craftingTable = bot.findBlock({
                matching: mcData.blocksByName.crafting_table.id,
                maxDistance: 4
            });
            if (!craftingTable) {
                return res.status(400).json({ status: 'error', message: `Crafting ${itemName} requires a crafting table nearby, but none was found.` });
            }
        }

        const craftCount = count || 1;
        await bot.craft(recipe, craftCount, craftingTable);
        
        res.json({ status: 'success', message: `Successfully crafted ${craftCount} ${itemName}(s).` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// 9. Fight hostile mobs or specific entity types
app.post('/api/fight', async (req, res) => {
    const { mobName } = req.body; // e.g. "zombie", "skeleton", "spider"
    try {
        // Broaden filter: check name, mob type, or username/entity type
        const targetMob = bot.nearestEntity(entity => {
            if (!entity || !entity.position) return false;
            const distance = entity.position.distanceTo(bot.entity.position);
            if (distance > 20) return false; // within 20 blocks

            // Match by exact name, type, or partial string match
            const nameMatch = entity.name && entity.name.toLowerCase().includes(mobName.toLowerCase());
            const typeMatch = entity.type === 'mob' && mobName.toLowerCase() === 'mob';
            
            return nameMatch || typeMatch;
        });

        if (!targetMob) {
            return res.status(404).json({ status: 'error', message: `No nearby ${mobName} found within 20 blocks.` });
        }

        // Try to equip a sword or axe if available in inventory
        const weapon = bot.inventory.items().find(i => i.name.includes('sword') || i.name.includes('axe'));
        if (weapon) {
            await bot.equip(weapon, 'hand');
        }

        // Start attacking the target using the pvp plugin
        bot.pvp.attack(targetMob);
        
        res.json({ status: 'success', message: `Engaged combat with ${targetMob.name || mobName}!` });
    } catch (err) {
        res.status(500).json({ status: 'error', message: err.message });
    }
});

// Start the Bridge Server
const PORT = 3000;
app.listen(PORT, () => {
    console.log(`MC Bridge server running on http://localhost:${PORT}`);
});