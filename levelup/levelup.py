import asyncio
import datetime
import io
import logging
import math
import random
import sys
import typing
import tabulate
import json
import aiohttp

import discord
import matplotlib
import matplotlib.pyplot as plt
import validators
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box

from .formatter import (
    time_formatter,
    hex_to_rgb,
    get_level,
    get_xp,
    time_to_level,
    get_user_position,
    get_user_stats,
    profile_embed,
)
from .generator import Generator
from .menus import menu, DEFAULT_CONTROLS

matplotlib.use("agg")
plt.switch_backend("agg")
log = logging.getLogger("red.vrt.levelup")
LOADING = "https://i.imgur.com/l3p6EMX.gif"


# CREDITS
# Thanks aikaterna#1393 and epic guy#0715 for the caching advice :)
# Thanks Fixator10#7133 for having a Leveler cog to get a reference for what kinda settings a leveler cog might need!
# Thanks crayyy_zee#2900 for showing me the dislash repo that i yoinked and did dirty things to
# Thanks Zephyrkul#1089 for the help with leaderboard formatting!

class LevelUp(commands.Cog):
    """Local Discord Leveling System"""
    __author__ = "Vertyco#0117"
    __version__ = "1.0.0"

    def format_help_for_context(self, ctx):
        helpcmd = super().format_help_for_context(ctx)
        info = f"{helpcmd}\n" \
               f"Cog Version: {self.__version__}\n" \
               f"Author: {self.__author__}\n" \
               f"Contributors: aikaterna#1393"
        return info

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, 117117117, force_registration=True)
        default_guild = {
            "users": {},  # All user level data
            "levelroles": {},  # Roles associated with levels
            "ignoredchannels": [],  # Channels that dont gain XP
            "ignoredroles": [],  # Roles that dont gain XP
            "ignoredusers": [],  # Ignored users wont gain XP
            "prestige": 0,  # Level required to prestige, 0 is disabled
            "prestigedata": {},  # Prestige tiers, the role associated with them, and emoji for them
            "xp": [3, 6],  # Min/Max XP per message
            "voicexp": 2,  # XP per minute in voice
            "cooldown": 60,  # Only gives XP every 30 seconds
            "base": 100,  # Base denominator for level algorithm, higher takes longer to level
            "exp": 2,  # Exponent for level algorithm, higher is a more exponential/steeper curve
            "length": 0,  # Minimum length of message to be considered eligible for XP gain
            "starcooldown": 3600,  # Cooldown in seconds for users to give eachother stars
            "usepics": False,  # Use Pics instead of embeds for leveling, Embeds are default
            "autoremove": False,  # Remove previous role on level up
            "stackprestigeroles": True,  # Toggle whether to stack prestige roles
            "muted": True,  # Ignore XP while being muted in voice
            "solo": True,  # Ignore XP while in a voice chat alone
            "deafened": True,  # Ignore XP while deafened in a voice chat
            "invisible": True,  # Ignore XP while status is invisible in voice chat
            "notifydm": False,  # Toggle notify member of level up in DMs
            "mention": False,  # Toggle whether to mention the user
            "notifylog": None,  # Notify member of level up in a set channel
        }
        self.config.register_guild(**default_guild)

        # Guild id's as strings, user id's as strings
        self.settings = {}  # Cache settings
        self.cache = {}  # Dumps to config every 60 seconds
        self.lastmsg = {}  # Last sent message for users
        self.voice = {}  # Voice channel info
        self.stars = {}  # Keep track of star cooldowns

        # For importing user levels from Fixator's Leveler cog
        self._db_ready = False
        self.client = None
        self.db = None

        # Cachey wakey dumpy wumpy loopy woopy
        self.cache_dumper.start()
        self.voice_checker.start()

    def cog_unload(self):
        self.cache_dumper.cancel()
        self.voice_checker.cancel()

    # Generate rinky dink profile image
    @staticmethod
    async def gen_profile_img(args: dict):
        image = await Generator().generate_profile(**args)
        file = discord.File(fp=image, filename=f"image_{random.randint(1000, 99999)}.webp")
        return file

    # Generate rinky dink level up image
    @staticmethod
    async def gen_levelup_img(args: dict):
        image = await Generator().generate_levelup(**args)
        file = discord.File(fp=image, filename=f"image_{random.randint(1000, 99999)}.webp")
        return file

    # Add a user to cache
    async def cache_user(self, guild: str, user: str):
        if guild not in self.cache:  # Alredy in init_settings but just in case
            self.cache[guild] = {}
        self.cache[guild][user] = {
            "xp": 0,
            "voice": 0,  # Seconds
            "messages": 0,
            "level": 0,
            "prestige": 0,
            "emoji": None,
            "background": None,
            "stars": 0
        }

    # Hacky way to get user banner
    async def get_banner(self, user: discord.Member) -> str:
        req = await self.bot.http.request(discord.http.Route("GET", "/users/{uid}", uid=user.id))
        banner_id = req["banner"]
        if banner_id:
            banner_url = f"https://cdn.discordapp.com/banners/{user.id}/{banner_id}?size=1024"
            return banner_url

    # Dump cache to config
    async def dump_cache(self):
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            if guild_id not in self.cache:
                continue
            if self.cache[guild_id]:  # If there is anything to cache
                conf = self.settings[guild_id]
                base = conf["base"]
                exp = conf["exp"]
                async with self.config.guild(guild).users() as users:
                    for user, data in self.cache[guild_id].items():
                        if user not in users:
                            users[user] = data
                        else:
                            users[user]["xp"] += data["xp"]
                            users[user]["voice"] += data["voice"]
                            users[user]["messages"] += data["messages"]
                        saved_level = users[user]["level"]
                        new_level = get_level(int(users[user]["xp"]), base, exp)
                        if str(new_level) != str(saved_level):
                            if "background" in users[user]:
                                bg = users[user]["background"]
                                await self.level_up(guild, user, new_level, bg)
                            else:
                                await self.level_up(guild, user, new_level)
                            users[user]["level"] = new_level
                    self.cache[guild_id].clear()

    # User has leveled up, send message and check if any roles are associated with it
    async def level_up(self, guild: discord.guild, user: str, new_level: int, bg: str = None):
        conf = self.settings[str(guild.id)]
        levelroles = conf["levelroles"]
        roleperms = guild.me.guild_permissions.manage_roles
        if not roleperms:
            log.warning(f"Bot can't manage roles in {guild.name}")
        autoremove = conf["autoremove"]
        dm = conf["notifydm"]
        mention = conf["mention"]
        channel = conf["notifylog"]
        usepics = conf["usepics"]
        member = guild.get_member(int(user))
        if not member:
            return
        # Send levelup messages
        if not usepics:
            if dm:
                await member.send(f"You have just reached level {new_level} in {guild.name}!")
            if channel:
                channel = guild.get_channel(channel)
                name = member.name
                mentionuser = member.mention
                color = member.colour
                pfp = member.avatar_url
                if mention and channel:
                    send = channel.permissions_for(guild.me).send_messages
                    if send:
                        await channel.send(f"{mentionuser}")
                embed = discord.Embed(
                    description=f"**Just reached level {new_level}!**",
                    color=color
                )
                embed.set_author(name=name, icon_url=pfp)
                if channel:
                    send = channel.permissions_for(guild.me).send_messages
                    if send:
                        await channel.send(embed=embed)
                    else:
                        log.warning(f"Bot cant send LevelUp alert to {channel.name} in {guild.name}")
        else:
            # Generate LevelUP Image
            if bg:
                banner = bg
            else:
                banner = await self.get_banner(member)
            color = str(member.colour)
            if color == "#000000":  # Don't use default color
                color = str(discord.Color.random())
            color = hex_to_rgb(color)
            args = {
                'bg_image': banner,
                'profile_image': member.avatar_url,
                'level': new_level,
                'color': color,
            }
            if dm:
                file = await self.gen_levelup_img(args)
                await member.send(f"You just leveled up in {guild.name}!", file=file)
            if channel:
                channel = guild.get_channel(channel)
                if mention:
                    person = member.mention
                else:
                    person = member.name
                if channel:
                    send = channel.permissions_for(guild.me).send_messages
                    if send:
                        file = await self.gen_levelup_img(args)
                        await channel.send(f"**{person} just leveled up!**", file=file)
                    else:
                        log.warning(f"Bot cant send LevelUp alert to log channel in {guild.name}")

        # Role adding/removal
        if roleperms and levelroles:
            if str(new_level) in levelroles:
                role_id = levelroles[str(new_level)]
                role = guild.get_role(int(role_id))
                if not role:
                    return
                if role not in member.roles:
                    await member.add_roles(role)
            if new_level > 1 and autoremove:
                for role in member.roles:
                    for level, role_id in levelroles:
                        if int(level) < new_level and str(role.id) == str(role_id):
                            await member.remove_roles(role)

    # Cache main settings
    async def init_settings(self):
        for guild in self.bot.guilds:
            settings = await self.config.guild(guild).all()
            guild_id = str(guild.id)
            if guild_id not in self.settings:
                self.settings[guild_id] = {}
            for k, v in settings.items():
                if k != "users":
                    self.settings[guild_id][k] = v
            if guild_id not in self.cache:
                self.cache[guild_id] = {}
            if guild_id not in self.stars:
                self.stars[guild_id] = {}
            if guild_id not in self.voice:
                self.voice[guild_id] = {}

    @commands.Cog.listener("on_message")
    async def messages(self, message: discord.Message):
        # If message was from a bot
        if message.author.bot:
            return
        # If message wasn't sent in a guild
        if not message.guild:
            return
        # If message has no content for some reason?
        if not message:
            return
        return await self.message_handler(message)

    async def message_handler(self, message: discord.Message):
        now = datetime.datetime.now()
        guild = message.guild
        guild_id = str(guild.id)
        if guild_id not in self.cache:
            return
        user = str(message.author.id)
        conf = self.settings[guild_id]
        xpmin = int(conf["xp"][0])
        xpmax = int(conf["xp"][1]) + 1
        xp = random.choice(range(xpmin, xpmax))
        addxp = False
        if user not in self.cache[guild_id]:
            await self.cache_user(guild_id, user)
        if user not in self.lastmsg:
            self.lastmsg[user] = now
            addxp = True
        td = now - self.lastmsg[user]
        td = int(td.total_seconds())
        if td > conf["cooldown"]:
            addxp = True
        for role in message.author.roles:
            if role.id in conf["ignoredroles"]:
                addxp = False
        if message.channel.id in conf["ignoredchannels"]:
            addxp = False
        if int(user) in conf["ignoredusers"]:
            addxp = False
        if conf["length"]:  # Make sure message meets minimum length requirements
            if len(message.content) < conf["length"]:
                addxp = False
        if addxp:  # Give XP
            self.cache[guild_id][user]["xp"] += xp
        self.cache[guild_id][user]["messages"] += 1

    async def check_voice(self):
        for guild in self.bot.guilds:
            guild_id = str(guild.id)
            if guild_id not in self.settings:
                self.settings[guild_id] = {}
                continue
            conf = self.settings[guild_id]
            xp_per_minute = conf["voicexp"]
            if guild_id not in self.voice:
                self.voice[guild_id] = {}
            now = datetime.datetime.now()
            for member in guild.members:
                if member.bot:
                    continue
                voice_state = member.voice
                if not voice_state:  # Only cache if user is in a vc
                    continue
                user_id = str(member.id)
                if user_id not in self.voice[guild_id]:
                    self.voice[guild_id][user_id] = now
                if user_id not in self.cache[guild_id]:
                    await self.cache_user(guild_id, user_id)
                ts = self.voice[guild_id][user_id]
                td = now - ts
                td = int(td.total_seconds())
                xp_to_give = (td / 60) * xp_per_minute
                addxp = True
                if conf["muted"] and voice_state.self_mute:
                    addxp = False
                if conf["deafened"] and voice_state.self_deaf:
                    addxp = False
                if conf["invisible"] and member.status.name == "offline":
                    addxp = False
                if conf["solo"] and len(voice_state.channel.members) == 1:
                    addxp = False
                for role in member.roles:
                    if role.id in conf["ignoredroles"]:
                        addxp = False
                if int(user_id) in conf["ignoredusers"]:
                    addxp = False
                if voice_state.channel.id in conf["ignoredchannels"]:
                    addxp = False
                if addxp:
                    self.cache[guild_id][user_id]["xp"] += xp_to_give
                self.cache[guild_id][user_id]["voice"] += td
                self.voice[guild_id][user_id] = now

    @tasks.loop(seconds=15)
    async def voice_checker(self):
        await self.check_voice()

    @voice_checker.before_loop
    async def before_voice_checker(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(5)

    @tasks.loop(seconds=30)
    async def cache_dumper(self):
        await self.dump_cache()

    @cache_dumper.before_loop
    async def before_dumper(self):
        await self.bot.wait_until_red_ready()
        await self.init_settings()
        await asyncio.sleep(10)

    @commands.group(name="levelset", aliases=["lset"])
    @commands.admin()
    @commands.guild_only()
    async def lvl_group(self, ctx: commands.Context):
        """Access LevelUP setting commands"""
        pass

    @lvl_group.command(name="seelevels")
    async def see_levels(self, ctx: commands.Context):
        """
        Test the level algorith
        View the first 20 levels using the current algorithm to test experience curve
        """
        conf = await self.config.guild(ctx.guild).all()
        base = conf["base"]
        exp = conf["exp"]
        cd = conf["cooldown"]
        xp_range = conf["xp"]
        msg = ""
        table = []
        x = []
        y = []
        for i in range(1, 21):
            xp = get_xp(i, base, exp)
            msg += f"Level {i}: {xp} XP Needed\n"
            time = time_to_level(i, base, exp, cd, xp_range)
            time = time_formatter(time)
            table.append([i, xp, time])
            x.append(i)
            y.append(xp)
        headers = ["Level", "XP Needed", "AproxTime"]
        data = tabulate.tabulate(table, headers, tablefmt="presto")
        with plt.style.context("dark_background"):
            plt.plot(x, y, color="xkcd:green", label="Total", linewidth=0.7)
            plt.xlabel(f"Level", fontsize=10)
            plt.ylabel(f"Experience", fontsize=10)
            plt.title("XP Curve")
            plt.grid(axis="y")
            plt.grid(axis="x")
            result = io.BytesIO()
            plt.savefig(result, format="png", dpi=200)
            plt.close()
            result.seek(0)
            file = discord.File(result, filename="lvlexample.png")
            img = "attachment://lvlexample.png"
        example = "XP required for a level = Base * Level^Exp\n\n" \
                  "Approx time is the time it would take for a user to reach a level if they " \
                  "typed every time the cooldown expired non stop without sleeping or taking " \
                  "potty breaks."
        embed = discord.Embed(
            title="Level Example",
            description=f"`Base Multiplier:  `{base}\n"
                        f"`Exp Multiplier:   `{exp}\n"
                        f"`Experience Range: `{xp_range}\n"
                        f"`Message Cooldown: `{cd}\n"
                        f"{box(example)}\n"
                        f"{box(data, lang='python')}",
            color=discord.Color.random()
        )
        embed.set_image(url=img)
        await ctx.send(embed=embed, file=file)

    @lvl_group.command(name="view")
    async def view_settings(self, ctx: commands.Context):
        """View all LevelUP settings"""
        conf = await self.config.guild(ctx.guild).all()
        levelroles = conf["levelroles"]
        igchannels = conf["ignoredchannels"]
        igroles = conf["ignoredroles"]
        igusers = conf["ignoredusers"]
        prestige = conf["prestige"]
        pdata = conf["prestigedata"]
        stacking = conf["stackprestigeroles"]
        xp = conf["xp"]
        voicexp = conf["voicexp"]
        cooldown = conf["cooldown"]
        base = conf["base"]
        exp = conf["exp"]
        length = conf["length"]
        autoremove = conf["autoremove"]
        muted = conf["muted"]
        solo = conf["solo"]
        deafended = conf["deafened"]
        invisible = conf["invisible"]
        notifydm = conf["notifydm"]
        mention = conf["mention"]
        starcooldown = conf["starcooldown"]
        sc = time_formatter(starcooldown)
        notifylog = ctx.guild.get_channel(conf["notifylog"])
        if not notifylog:
            notifylog = conf["notifylog"]
        else:
            notifylog = notifylog.mention
        msg = f"**Messages**\n" \
              f"`Message XP:       `{xp[0]}-{xp[1]}\n" \
              f"`Min Msg Length:   `{length}\n" \
              f"`Cooldown:         `{cooldown} seconds\n" \
              f"**Voice**\n" \
              f"`Voice XP:         `{voicexp} per minute\n" \
              f"`Ignore Muted:     `{muted}\n" \
              f"`Ignore Solo:      `{solo}\n" \
              f"`Ignore Deafened:  `{deafended}\n" \
              f"`Ignore Invisible: `{invisible}\n" \
              f"`AutoRemove Roles: `{autoremove}\n" \
              f"**Level Algorithm**\n" \
              f"`Base Multiplier:  `{base}\n" \
              f"`Exp Multiplier:   `{exp}\n" \
              f"**LevelUps**\n" \
              f"`Notify in DMs:    `{notifydm}\n" \
              f"`Mention User:     `{mention}\n" \
              f"`LevelUp Channel:  `{notifylog}\n" \
              f"**Stars**\n" \
              f"`Cooldown:         `{sc}\n"
        if levelroles:
            msg += "**Levels**\n"
            for level, role_id in levelroles.items():
                role = ctx.guild.get_role(role_id)
                if role:
                    role = role.mention
                else:
                    role = role_id
                msg += f"`Level {level}: `{role}\n"
        if igchannels:
            msg += "**Ignored Channels**\n"
            for channel_id in igchannels:
                channel = ctx.guild.get_channel(channel_id)
                if channel:
                    channel = channel.mention
                else:
                    channel = channel_id
                msg += f"{channel}\n"
        if igroles:
            msg += "**Ignored Roles**\n"
            for role_id in igroles:
                role = ctx.guild.get_role(role_id)
                if role:
                    role = role.mention
                else:
                    role = role_id
                msg += f"{role}\n"
        if igusers:
            msg += "**Ignored Users**\n"
            for user_id in igusers:
                user = ctx.guild.get_member(user_id)
                if user:
                    user = user.mention
                else:
                    user = user_id
                msg += f"{user}\n"
        if prestige and pdata:
            msg += "**Prestige**\n" \
                   f"`Stack Roles: `{stacking}\n" \
                   f"`Level Req:  `{prestige}\n"
            for level, data in pdata.items():
                role_id = data["role"]
                role = ctx.guild.get_role(role_id)
                if role:
                    role = role.mention
                else:
                    role = role_id
                emoji = data["emoji"]
                msg += f"`Prestige {level}: `{role} - {emoji}\n"
        embed = discord.Embed(
            title="LevelUp Settings",
            description=msg,
            color=discord.Color.random()
        )
        await ctx.send(embed=embed)

    @lvl_group.command(name="fullreset")
    @commands.is_owner()
    async def reset_all(self, ctx: commands.Context):
        """Reset entire cog user data"""
        for guild in self.bot.guilds:
            await self.config.guild(guild).users.set({})
            await ctx.tick()

    @lvl_group.command(name="reset")
    @commands.guildowner()
    async def reset_guild(self, ctx: commands.Context):
        """Reset guild user data"""
        await self.config.guild(ctx.guild).users.set({})
        await ctx.tick()

    @lvl_group.command(name="fullbackup")
    @commands.is_owner()
    async def backup_all_settings(self, ctx: commands.Context):
        """Sends a full backup of the config as a JSON file to Discord."""
        settings = await self.config.all_guilds()
        settings = json.dumps(settings)
        with open(f"{ctx.guild}.json", "w") as file:
            file.write(settings)
        with open(f"{ctx.guild}.json", "rb") as file:
            await ctx.send(file=discord.File(file, f"LevelUp_full_config.json"))

    @lvl_group.command(name="backup")
    @commands.guildowner()
    async def backup_settings(self, ctx: commands.Context):
        """
        Make a backup of your config

        Sends the .json to discord
        """
        settings = await self.config.guild(ctx.guild).all()
        settings = json.dumps(settings)
        with open(f"{ctx.guild}.json", "w") as file:
            file.write(settings)
        with open(f"{ctx.guild}.json", "rb") as file:
            await ctx.send(file=discord.File(file, f"{ctx.guild}_config.json"))

    @lvl_group.command(name="fullrestore")
    @commands.is_owner()
    async def restore_all_settings(self, ctx: commands.Context):
        """Upload a backup JSON file attached to this command to restore the full config."""
        if ctx.message.attachments:
            attachment_url = ctx.message.attachments[0].url
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_url) as resp:
                    config = await resp.json()
            for guild in self.bot.guilds:
                async with self.config.guild(guild).all() as conf:
                    guild_id = str(guild.id)
                    if guild_id in config:
                        conf["users"] = config[guild_id]["users"]
            await self.init_settings()
            return await ctx.send("Config restored from backup file!")
        else:
            return await ctx.send("Attach your backup file to the message when using this command.")

    @lvl_group.command(name="restore")
    @commands.guildowner()
    async def restore_settings(self, ctx: commands.Context):
        """
        Restore your LevelUp backup config

        Attach the .json file that you made when using the command to import it
        """
        if ctx.message.attachments:
            attachment_url = ctx.message.attachments[0].url
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment_url) as resp:
                    config = await resp.json()
            await self.config.guild(ctx.guild).set(config)
            await self.init_settings()
            return await ctx.send("Config restored from backup file!")
        else:
            return await ctx.send("Attach your backup file to the message when using this command.")

    @lvl_group.command(name="cache")
    @commands.is_owner()
    async def get_cache_size(self, ctx: commands.Context):
        """See how much RAM this cog's cache is using"""
        s = sys.getsizeof(self.settings)
        c = sys.getsizeof(self.cache)
        lm = sys.getsizeof(self.lastmsg)
        v = sys.getsizeof(self.voice)
        st = sys.getsizeof(self.stars)
        total = sum([s, c, lm, v, st])
        bytestring = "{:,}".format(total)
        kb = int(total / 1000)
        kbstring = "{:,}".format(kb)
        mb = int(kb / 1000)
        mbstring = "{:,}".format(mb)
        sizes = f"{bytestring} bytes\n" \
                f"{kbstring} Kb\n" \
                f"{mbstring} Mb\n"
        await ctx.send(f"**Total Cache Size**\n{box(sizes)}")

    @lvl_group.command(name="importleveler")
    @commands.is_owner()
    async def import_from_leveler(self, ctx: commands.Context, yes_or_no: str):
        """
        Import data from Fixator's Leveler cog

        This will overwrite existing LevelUp level data and stars
        It will also import XP range level roles, and ignored channels
        *Obviously you will need Leveler loaded while you run this command*
        """
        if "y" not in yes_or_no:
            return await ctx.send("Not importing users")
        leveler = self.bot.get_cog("Leveler")
        if not leveler:
            return await ctx.send("Leveler is not loaded, please load it and try again!")
        config = await leveler.config.custom("MONGODB").all()
        if not config:
            return await ctx.send("Couldnt find mongo config")

        # If leveler is installed then libs should import fine
        try:
            import subprocess
            from motor.motor_asyncio import AsyncIOMotorClient
            from pymongo import errors as mongoerrors
        except Exception as e:
            log.warning(f"pymongo Import Error: {e}")
            return await ctx.send("Failed to import modules")

        # Try connecting to mongo
        if self._db_ready:
            self._db_ready = False
        self._disconnect_mongo()
        try:
            self.client = AsyncIOMotorClient(
                **{k: v for k, v in config.items() if not k == "db_name"}
            )
            await self.client.server_info()
            self.db = self.client[config["db_name"]]
            self._db_ready = True
        except (
            mongoerrors.ServerSelectionTimeoutError,
            mongoerrors.ConfigurationError,
            mongoerrors.OperationFailure,
        ) as e:
            log.warning(f"Failed to connect to MongoDB: {e}")
            self.client = None
            self.db = None
            return await ctx.send("Failed to connect to MongoDB")

        # If everything is okay so far let the user know its working
        embed = discord.Embed(
            description=f"Importing users from Leveler...",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=LOADING)
        msg = await ctx.send(embed=embed)
        users_imported = 0
        # Now to start the importing
        async with ctx.typing():
            min_message_length = await leveler.config.message_length()
            mention = await leveler.config.mention()
            xp_range = await leveler.config.xp()
            for guild in self.bot.guilds:
                guild_id = str(guild.id)
                async with self.config.guild(guild).all() as conf:

                    # IMPORT COG SETTINGS
                    ignored_channels = await leveler.config.guild(guild).ignored_channels()
                    if ignored_channels:
                        conf["ignoredchannels"] = ignored_channels
                    if min_message_length:
                        conf["length"] = int(min_message_length)
                    if mention:
                        conf["mention"] = True
                    if xp_range:
                        conf["xp"] = xp_range
                    server_roles = await self.db.roles.find_one({"server_id": guild_id})
                    if server_roles:
                        for rolename, data in server_roles["roles"].items():
                            role = guild.get_role(rolename)
                            if not role:
                                continue
                            level_req = data["level"]
                            conf["levelroles"][level_req] = role.id

                    # IMPORT USER DATA
                    users = conf["users"]
                    for user in guild.members:
                        user_id = str(user.id)
                        if user_id not in users:
                            continue
                        try:
                            userinfo = await self.db.users.find_one({"user_id": user_id})
                        except Exception as e:
                            log.info(f"No data found for {user.name}: {e}")
                            continue
                        if not userinfo:
                            continue
                        rep = userinfo["rep"]
                        servers = userinfo["servers"]
                        if guild_id in servers:
                            level = servers[guild_id]["level"]
                        else:
                            level = None
                        if "stars" in users[user_id]:
                            users[user_id]["stars"] = int(rep)
                        if level:
                            base = conf["base"]
                            exp = conf["exp"]
                            xp = get_xp(level, base, exp)
                            users[user_id]["level"] = int(level)
                            users[user_id]["xp"] = xp
                        if level or "stars" in users[user_id]:
                            users_imported += 1
            embed = discord.Embed(
                description=f"Importing Complete!\n"
                            f"{users_imported} users imported",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=LOADING)
            await msg.edit(embed=embed)
            self._disconnect_mongo()

    def _disconnect_mongo(self):
        if self.client:
            self.client.close()

    @lvl_group.command(name="cleanup")
    @commands.guildowner()
    async def cleanup_guild(self, ctx: commands.Context):
        """Delete users no longer in the server"""
        guild = ctx.guild
        members = guild.members
        cleanup = []
        users = await self.config.guild(ctx.guild).users()
        for user_id in users:
            user = guild.get_member(int(user_id))
            if not user:  # Banish the heretics
                cleanup.append(user_id)
            elif user not in members:  # Also banish the heretics
                cleanup.append(user_id)
            elif user.bot:  # Cleanup my noob mistakes
                cleanup.append(user_id)
            else:
                continue
        if not cleanup:
            return await ctx.send("Nothing to clean")
        async with self.config.guild(ctx.guild).users() as users:
            cleaned = 0
            for uid in cleanup:
                del users[uid]
                cleaned += 1
        await ctx.send(f"Deleted {cleaned} user ID's from the config that are no longer in the server")

    @lvl_group.command(name="xp")
    async def set_xp(self, ctx: commands.Context, min_xp: int = 3, max_xp: int = 6):
        """
        Set message XP range
        Set the Min and Max amount of XP that a message can gain
        """
        xp = [min_xp, max_xp]
        await self.config.guild(ctx.guild).xp.set(xp)
        await ctx.send(f"Message XP range has been set to {min_xp} - {max_xp} per valid message")
        await self.init_settings()

    @lvl_group.command(name="voicexp")
    async def set_voice_xp(self, ctx: commands.Context, voice_xp: int):
        """
        Set voice XP gain
        Sets the amount of XP gained per minute in a voice channel (default is 2)
        """
        await self.config.guild(ctx.guild).voicexp.set(voice_xp)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.command(name="cooldown")
    async def set_cooldown(self, ctx: commands.Context, cooldown: int):
        """
        Cooldown threshold for message XP

        When a user sends a message they will have to wait X seconds before their message
        counts as XP gained
        """
        await self.config.guild(ctx.guild).cooldown.set(cooldown)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.command(name="base")
    async def set_base(self, ctx: commands.Context, base_multiplier: int):
        """
        Base multiplier for the leveling algorithm

        Affects leveling on a more linear scale(higher values makes leveling take longer)
        """
        await self.config.guild(ctx.guild).base.set(base_multiplier)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.command(name="exp")
    async def set_exp(self, ctx: commands.Context, exponent_multiplier: typing.Union[int, float]):
        """
        Exponent multiplier for the leveling algorithm

        Affects leveling on an exponential scale(higher values makes leveling take exponentially longer)
        """
        await self.config.guild(ctx.guild).exp.set(exponent_multiplier)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.command(name="length")
    async def set_length(self, ctx: commands.Context, minimum_length: int):
        """
        Set minimum message length for XP
        Minimum length a message must be to count towards XP gained

        Set to 0 to disable
        """
        await self.config.guild(ctx.guild).length.set(minimum_length)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.command(name="embeds")
    async def toggle_embeds(self, ctx: commands.Context):
        """Toggle useing embeds or generated pics"""
        usepics = await self.config.guild(ctx.guild).usepics()
        if usepics:
            await self.config.guild(ctx.guild).usepics.set(False)
            await ctx.send("LevelUp will now use embeds instead of generated images")
        else:
            await self.config.guild(ctx.guild).usepics.set(True)
            await ctx.send("LevelUp will now use generated images instead of embeds")
        await self.init_settings()

    @lvl_group.command(name="autoremove")
    async def toggle_autoremove(self, ctx: commands.Context):
        """Automatic removal of previous level roles"""
        autoremove = await self.config.guild(ctx.guild).autoremove()
        if autoremove:
            await self.config.guild(ctx.guild).autoremove.set(False)
            await ctx.send("Automatic role removal **Disabled**")
        else:
            await self.config.guild(ctx.guild).autoremove.set(True)
            await ctx.send("Automatic role removal **Enabled**")
        await self.init_settings()

    @lvl_group.command(name="muted")
    async def ignore_muted(self, ctx: commands.Context):
        """
        Ignore muted voice users
        Toggle whether self-muted users in a voice channel can gain voice XP
        """
        muted = await self.config.guild(ctx.guild).muted()
        if muted:
            await self.config.guild(ctx.guild).muted.set(False)
            await ctx.send("Self-Muted users can now gain XP while in a voice channel")
        else:
            await self.config.guild(ctx.guild).muted.set(True)
            await ctx.send("Self-Muted users can no longer gain XP while in a voice channel")
        await self.init_settings()

    @lvl_group.command(name="solo")
    async def ignore_solo(self, ctx: commands.Context):
        """
        Ignore solo voice users
        Toggle whether solo users in a voice channel can gain voice XP
        """
        solo = await self.config.guild(ctx.guild).solo()
        if solo:
            await self.config.guild(ctx.guild).solo.set(False)
            await ctx.send("Solo users can now gain XP while in a voice channel")
        else:
            await self.config.guild(ctx.guild).solo.set(True)
            await ctx.send("Solo users can no longer gain XP while in a voice channel")
        await self.init_settings()

    @lvl_group.command(name="deafened")
    async def ignore_deafened(self, ctx: commands.Context):
        """
        Ignore deafened voice users
        Toggle whether deafened users in a voice channel can gain voice XP
        """
        deafened = await self.config.guild(ctx.guild).deafened()
        if deafened:
            await self.config.guild(ctx.guild).deafened.set(False)
            await ctx.send("Deafened users can now gain XP while in a voice channel")
        else:
            await self.config.guild(ctx.guild).deafened.set(True)
            await ctx.send("Deafened users can no longer gain XP while in a voice channel")
        await self.init_settings()

    @lvl_group.command(name="invisible")
    async def ignore_invisible(self, ctx: commands.Context):
        """
        Ignore invisible voice users
        Toggle whether invisible users in a voice channel can gain voice XP
        """
        invisible = await self.config.guild(ctx.guild).invisible()
        if invisible:
            await self.config.guild(ctx.guild).invisible.set(False)
            await ctx.send("Invisible users can now gain XP while in a voice channel")
        else:
            await self.config.guild(ctx.guild).invisible.set(True)
            await ctx.send("Invisible users can no longer gain XP while in a voice channel")
        await self.init_settings()

    @lvl_group.command(name="dm")
    async def toggle_dm(self, ctx: commands.Context):
        """
        Toggle DM notifications
        Toggle whether LevelUp messages are DM'd to the user
        """
        notifydm = await self.config.guild(ctx.guild).notifydm()
        if notifydm:
            await self.config.guild(ctx.guild).notifydm.set(False)
            await ctx.send("Users will no longer be DM'd when they level up")
        else:
            await self.config.guild(ctx.guild).notifydm.set(True)
            await ctx.send("Users will now be DM'd when they level up")
        await self.init_settings()

    @lvl_group.command(name="mention")
    async def toggle_mention(self, ctx: commands.Context):
        """
        Toggle levelup mentions
        Toggle whether the user in mentioned in LevelUp messages
        """
        mention = await self.config.guild(ctx.guild).mention()
        if mention:
            await self.config.guild(ctx.guild).mention.set(False)
            await ctx.send("Users will no longer be mentioned when they level up")
        else:
            await self.config.guild(ctx.guild).mention.set(True)
            await ctx.send("Users will now be mentioned when they level up")
        await self.init_settings()

    @lvl_group.command(name="levelchannel")
    async def set_level_channel(self, ctx: commands.Context, levelup_channel: discord.TextChannel = None):
        """
        Set LevelUP message channel
        Set a channel for all level up messages to send to
        """
        if not levelup_channel:
            await self.config.guild(ctx.guild).notifylog.set(None)
            await ctx.send("LevelUp channel has been **Disabled**")
        else:
            await self.config.guild(ctx.guild).notifylog.set(levelup_channel.id)
            await ctx.send(f"LevelUp channel has been set to {levelup_channel.mention}")
        await self.init_settings()

    @lvl_group.command(name="starcooldown")
    async def set_star_cooldown(self, ctx: commands.Context, time_in_seconds: int):
        """
        Set the star cooldown

        Users can give another user a star every X seconds
        """
        await self.config.guild(ctx.guild).starcooldown.set(time_in_seconds)
        await ctx.tick()
        await self.init_settings()

    @lvl_group.group(name="roles")
    async def level_roles(self, ctx: commands.Context):
        """Level role assignment"""

    @level_roles.command(name="add")
    async def add_level_role(self, ctx: commands.Context, level: str, role: discord.Role):
        """Assign a role to a level"""
        async with self.config.guild(ctx.guild).levelroles() as roles:
            if level in roles:
                overwrite = "Overwritten"
            else:
                overwrite = "Set"
            roles[level] = role.id
            await ctx.send(f"Level {level} has been {overwrite} as {role.mention}")
            await self.init_settings()

    @level_roles.command(name="del")
    async def del_level_role(self, ctx: commands.Context, level: str):
        """Assign a role to a level"""
        async with self.config.guild(ctx.guild).levelroles() as roles:
            if level in roles:
                del roles[level]
                await ctx.send("Level role has been deleted!")
            else:
                await ctx.send("Level doesnt exist!")
            await self.init_settings()

    @lvl_group.group(name="prestige")
    async def prestige_settings(self, ctx: commands.Context):
        """Level Prestige Settings"""
        pass

    @prestige_settings.command(name="level")
    async def prestige_level(self, ctx: commands.Context, level: int):
        """
        Set the level required to prestige
        Set to 0 to disable prestige
        """
        await self.config.guild(ctx.guild).prestige.set(level)
        await ctx.tick()
        await self.init_settings()

    @prestige_settings.command(name="addprestigedata")
    async def add_pres_data(
            self,
            ctx: commands.Context,
            prestige_level: int,
            role: discord.Role,
            emoji: str
    ):
        """
        Add prestige roles
        Add a role and emoji associated with a specific prestige level

        When a user prestiges, they will get that role and the emoji will show on their profile
        """
        async with self.config.guild(ctx.guild).prestigedata() as data:
            data[prestige_level] = {
                "role": role.id,
                "emoji": emoji
            }
        await ctx.tick()
        await self.init_settings()

    @prestige_settings.command(name="delprestigedata")
    async def del_pres_data(self, ctx: commands.Context, prestige_level: str):
        """Delete a prestige level"""
        async with self.config.guild(ctx.guild).prestigedata() as data:
            if prestige_level in data:
                del data[prestige_level]
            else:
                return await ctx.send("That prestige level doesnt exist!")
        await ctx.tick()
        await self.init_settings()

    @lvl_group.group(name="ignored")
    async def ignore_group(self, ctx: commands.Context):
        """Base command for all ignore lists"""
        pass

    @ignore_group.command(name="channel")
    async def ignore_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Add/Remove a channel from the ignore list
        Channels in the ignore list dont gain XP

        Use the command with a channel already in the ignore list to remove it
        """
        async with self.config.guild(ctx.guild).ignoredchannels() as ignored:
            if channel.id in ignored:
                ignored.remove(channel.id)
                await ctx.send("Channel removed from ignore list")
            else:
                ignored.append(channel.id)
                await ctx.send("Channel added to ignore list")
        await self.init_settings()

    @ignore_group.command(name="role")
    async def ignore_role(self, ctx: commands.Context, role: discord.Role):
        """
        Add/Remove a role from the ignore list
        Roles in the ignore list dont gain XP

        Use the command with a role already in the ignore list to remove it
        """
        async with self.config.guild(ctx.guild).ignoredroles() as ignored:
            if role.id in ignored:
                ignored.remove(role.id)
                await ctx.send("Role removed from ignore list")
            else:
                ignored.append(role.id)
                await ctx.send("Role added to ignore list")
        await self.init_settings()

    @ignore_group.command(name="member")
    async def ignore_member(self, ctx: commands.Context, member: discord.Member):
        """
        Add/Remove a member from the ignore list
        Members in the ignore list dont gain XP

        Use the command with a member already in the ignore list to remove them
        """
        async with self.config.guild(ctx.guild).ignoredusers() as ignored:
            if member.id in ignored:
                ignored.remove(member.id)
                await ctx.send("Member removed from ignore list")
            else:
                ignored.append(member.id)
                await ctx.send("Member added to ignore list")
        await self.init_settings()

    @commands.command(name="stars", aliases=["givestar", "addstar", "thanks"])
    @commands.guild_only()
    async def give_star(self, ctx: commands.Context, *, user: discord.Member):
        """
        Reward a good noodle
        Give a star to a user for being a good noodle
        """
        now = datetime.datetime.now()
        user_id = str(user.id)
        if ctx.author == user:
            return await ctx.send("**You can't give stars to yourself!**")
        if user.bot:
            return await ctx.send("**You can't give stars to a bot!**")
        if user_id not in self.stars[str(ctx.guild.id)]:
            self.stars[str(ctx.guild.id)][user_id] = now
        else:
            cooldown = self.settings[str(ctx.guild.id)]["starcooldown"]
            lastused = self.stars[str(ctx.guild.id)][user_id]
            td = now - lastused
            td = td.total_seconds()
            if td > cooldown:
                self.stars[str(ctx.guild.id)][user_id] = now
            else:
                time_left = cooldown - td
                tstring = time_formatter(time_left)
                msg = f"**You need to wait {tstring} before you can give more stars!**"
                return await ctx.send(msg)
        async with self.config.guild(ctx.guild).all() as conf:
            users = conf["users"]
            if user_id not in users:
                return await ctx.send("No data available for that user yet!")
            if "stars" not in users[user_id]:
                users[user_id]["stars"] = 1
            else:
                users[user_id]["stars"] += 1
            await ctx.send(f"**You just gave a star to {user.mention}!**")

    # For testing purposes
    @commands.command(name="mocklvl", hidden=True)
    async def get_lvl_test(self, ctx, *, user: discord.Member = None):
        """Get lvl"""
        if not user:
            user = ctx.author
        banner = await self.get_banner(user)
        color = str(user.colour)
        color = hex_to_rgb(color)
        args = {
            'bg_image': banner,
            'profile_image': user.avatar_url,
            'level': 69,
            'color': color,
        }
        file = await self.gen_levelup_img(args)
        await ctx.send(file=file)

    # For testing purposes
    @commands.command(name="mocklvlup", hidden=True)
    @commands.is_owner()
    async def mock_lvl_up(self, ctx, *, person: discord.Member = None):
        """Force level a user or yourself"""
        if not person:
            person = ctx.author
        user_id = str(person.id)
        guild_id = str(ctx.guild.id)
        if user_id not in self.cache[guild_id]:
            await self.cache_user(guild_id, user_id)
        conf = self.settings[guild_id]
        base = conf["base"]
        exp = conf["exp"]
        users = await self.config.guild(ctx.guild).users()
        user = users[user_id]
        currentxp = user["xp"]
        level = user["level"]
        level = level + 1
        new_xp = get_xp(level, base, exp)
        xp = new_xp - currentxp + 10
        self.cache[guild_id][user_id]["xp"] = xp
        await asyncio.sleep(2)
        await self.dump_cache()
        await ctx.send(f"Forced {person.name} to level up!")

    @commands.command(name="setmybg", aliases=["setbg"])
    async def set_user_background(self, ctx: commands.Context, image_url: str = None):
        """
        Set a background for your profile

        This will override your profile banner as the background

        **WARNING**
        Profile backgrounds are wide landscapes (900 by 240 pixels) and using a portrait image will be skewed

        Tip: Googling "dual monitor backgrounds" gives good results for the right images
        """
        # If image url is given, run some checks
        if image_url:
            valid = validators.url(image_url)
            if not valid:
                return await ctx.send("Uh Oh, looks like that is not a valid URL")
            try:
                # Try running it through profile generator blind to see if it errors
                args = {'bg_image': image_url, 'profile_image': ctx.author.avatar_url}
                await self.gen_profile_img(args)
            except Exception as e:
                if "cannot identify image file" in str(e):
                    return await ctx.send("Uh Oh, looks like that is not a valid image")
                else:
                    log.warning(f"background set failed: {e}")
                    return await ctx.send("Uh Oh, looks like that is not a valid image")
        user = ctx.author
        async with self.config.guild(ctx.guild).users() as users:
            if str(user.id) not in users:
                return await ctx.send("You aren't logged in the database yet, give it some time.")
            if image_url:
                users[str(user.id)]["background"] = image_url
                await ctx.send("Your image has been set!")
            else:
                if "background" in users[str(user.id)]:
                    del users[str(user.id)]["background"]
                    await ctx.send("Your background has been removed!")
                else:
                    await ctx.send(f"Nothing to delete, for help with this command, type `{ctx.prefix}help setmybg`")

    @commands.command(name="pf")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.guild_only()
    async def get_profile(self, ctx: commands.Context, *, user: discord.Member = None):
        """View your profile"""
        conf = await self.config.guild(ctx.guild).all()
        usepics = conf["usepics"]
        users = conf["users"]
        if not user:
            user = ctx.author
        if user.bot:
            return await ctx.send("Bots can't have profiles!")
        user_id = str(user.id)
        if user_id not in users:
            return await ctx.send("No information available yet!")
        pos = await get_user_position(conf, user_id)
        position = pos["p"]
        percentage = pos["pr"]
        stats = await get_user_stats(conf, user_id)
        level = stats["l"]
        messages = stats["m"]
        voice = stats["v"]
        xp = stats["xp"]
        goal = stats["goal"]
        progress = f'{"{:,}".format(xp)}/{"{:,}".format(goal)}'
        lvlbar = stats["lb"]
        lvlpercent = stats["lp"]
        emoji = stats["e"]
        prestige = stats["pr"]
        bg = stats["bg"]
        if "stars" in stats:
            stars = stats["stars"]
        else:
            stars = 0
        if not usepics:
            embed = await profile_embed(
                user,
                position,
                percentage,
                level,
                messages,
                voice,
                progress,
                lvlbar,
                lvlpercent,
                emoji,
                prestige,
                stars
            )
            await ctx.send(embed=embed)
        else:
            async with ctx.typing():
                if bg:
                    banner = bg
                else:
                    banner = await self.get_banner(user)
                color = str(user.colour)
                if color == "#000000":  # Don't use default color
                    color = str(discord.Color.random())
                color = hex_to_rgb(color)
                args = {
                    'bg_image': banner,  # Background image link
                    'profile_image': user.avatar_url,  # User profile picture link
                    'level': level,  # User current level
                    'current_xp': 0,  # Current level minimum xp
                    'user_xp': xp,  # User current xp
                    'next_xp': goal,  # xp required for next level
                    'user_position': position,  # User position in leaderboard
                    'user_name': user.name,  # user name with descriminator
                    'user_status': user.status.name,  # User status eg. online, offline, idle, streaming, dnd
                    'color': color,  # User's color
                    'messages': messages,
                    'voice': voice,
                    'prestige': prestige,
                    'stars': stars
                }
                file = await self.gen_profile_img(args)
                await ctx.send(file=file)

    @commands.command(name="prestige")
    @commands.guild_only()
    async def prestige_user(self, ctx: commands.Context):
        """
        Prestige your rank!
        Once you have reached this servers prestige level requirement, you can
        reset your stats to gain a prestige level and any perks associated with it
        """
        conf = await self.config.guild(ctx.guild).all()
        perms = ctx.channel.permissions_for(ctx.guild.me).manage_roles
        if not perms:
            log.warning("Insufficient perms to assign prestige ranks!")
        required_level = conf["prestige"]
        if not required_level:
            return await ctx.send("Prestige is disabled on this server!")
        prestige_data = conf["prestigedata"]
        if not prestige_data:
            return await ctx.send("Prestige levels have not been set yet!")
        user_id = str(ctx.author.id)
        users = conf["users"]
        if user_id not in users:
            return await ctx.send("No information available for you yet!")
        user = users[user_id]
        current_level = user["level"]
        prestige = user["prestige"]
        pending_prestige = str(prestige + 1)
        # First add new prestige role
        if current_level >= required_level:
            if pending_prestige in prestige_data:
                role = prestige_data["role"]
                rid = role
                emoji = prestige_data["emoji"]
                if perms:
                    role = ctx.guild.get_role(role)
                    if role:
                        await ctx.author.add_roles(role)
                    else:
                        log.warning(f"Prestige {pending_prestige} role ID: {rid} no longer exists!")
                async with self.config.guild(ctx.guild).all() as conf:
                    conf[user_id]["prestige"] = pending_prestige
                    conf[user_id]["emoji"] = emoji
            else:
                return await ctx.send(f"Prestige level {pending_prestige} has not been set yet!")
        else:
            msg = f"**You are not eligible to prestige yet!**\n" \
                  f"`Your level:     `{current_level}\n" \
                  f"`Required Level: `{required_level}"
            embed = discord.Embed(
                description=msg,
                color=discord.Color.red()
            )
            return await ctx.send(embed=embed)

        # Then remove old prestige role if autoremove is toggled
        if prestige > 0 and conf["stackprestigeroles"]:
            if str(prestige) in prestige_data:
                role_id = prestige_data[str(prestige)]["role"]
                role = ctx.guild.get_role(role_id)
                if role and perms:
                    await ctx.author.remove_roled(role)

    @commands.command(name="lvltop", aliases=["topstats", "membertop", "topranks"])
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context):
        """View the Leaderboard"""
        conf = await self.config.guild(ctx.guild).all()
        base = conf["base"]
        exp = conf["exp"]
        embeds = []
        prestige_req = conf["prestige"]
        leaderboard = {}
        total_messages = 0
        total_voice = 0
        for user, data in conf["users"].items():
            prestige = data["prestige"]
            xp = int(data["xp"])
            if prestige:
                add_xp = get_xp(prestige_req, base, exp)
                xp = int(xp + (prestige * add_xp))
            if xp > 0:
                leaderboard[user] = xp
            messages = data["messages"]
            voice = data["voice"]
            total_voice += voice
            total_messages += messages
        if not leaderboard:
            return await ctx.send("No user data yet!")
        voice = time_formatter(total_voice)
        sorted_users = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)

        # Get your place in the LB
        you = ""
        for i in sorted_users:
            uid = i[0]
            if str(uid) == str(ctx.author.id):
                i = sorted_users.index(i)
                you = f"You: {i + 1}/{len(sorted_users)}\n"

        pages = math.ceil(len(sorted_users) / 10)
        start = 0
        stop = 10
        longestxp = 1
        longestlvl = 1
        for p in range(pages):
            title = f"**Total Messages:** `{'{:,}'.format(total_messages)}`\n" \
                    f"**Total VoiceTime:** `{voice}`\n"
            msg = ""
            if stop > len(sorted_users):
                stop = len(sorted_users)
            for i in range(start, stop, 1):
                uid = sorted_users[i][0]
                user = ctx.guild.get_member(int(uid))
                if user:
                    user = user.name
                else:
                    user = uid
                xp = sorted_users[i][1]
                level = get_level(int(xp), base, exp)
                level = f"{level}"
                xp = f"{xp}"
                if i == 0:
                    longestxp = len(xp)
                    longestlvl = len(level)
                xplength = len(xp)
                if xplength < longestxp:
                    xp = xp.rjust(longestxp)
                lvlength = len(level)
                if lvlength < longestlvl:
                    level = level.rjust(longestlvl)
                if (i + 1) < 10:
                    msg += f"{i + 1}  ➤ Lvl {level}｜{xp} xp｜{user}\n"
                else:
                    msg += f"{i + 1} ➤ Lvl {level}｜{xp} xp｜{user}\n"
            embed = discord.Embed(
                title="LevelUp Leaderboard",
                description=f"{title}{box(msg, lang='python')}",
                color=discord.Color.random()
            )
            embed.set_thumbnail(url=ctx.guild.icon_url)
            if you:
                embed.set_footer(text=f"Pages {p + 1}/{pages} ｜ {you}")
            else:
                embed.set_footer(text=f"Pages {p + 1}/{pages}")
            embeds.append(embed)
            start += 10
            stop += 10
        if embeds:
            if len(embeds) == 1:
                embed = embeds[0]
                await ctx.send(embed=embed)
            else:
                await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            return await ctx.send("No user data yet!")

    @commands.command(name="startop", aliases=["starlb"])
    @commands.guild_only()
    async def star_leaderboard(self, ctx: commands.Context):
        """View the star leaderboard"""
        conf = await self.config.guild(ctx.guild).all()
        embeds = []
        leaderboard = {}
        total_stars = 0
        for user, data in conf["users"].items():
            if "stars" in data:
                stars = data["stars"]
                leaderboard[user] = stars
                total_stars += stars
        if not leaderboard:
            return await ctx.send("Nobody has stars yet 😕")
        sorted_users = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)

        # Get your place in the LB
        you = ""
        for i in sorted_users:
            uid = i[0]
            if str(uid) == str(ctx.author.id):
                i = sorted_users.index(i)
                you = f"You: {i + 1}/{len(sorted_users)}\n"

        pages = math.ceil(len(sorted_users) / 10)
        start = 0
        stop = 10
        for p in range(pages):
            title = f"**Star Leaderboard**\n" \
                    f"**Total ⭐'s: {total_stars}**\n"
            if stop > len(sorted_users):
                stop = len(sorted_users)
            table = []
            for i in range(start, stop, 1):
                uid = sorted_users[i][0]
                user = ctx.guild.get_member(int(uid))
                if user:
                    user = user.name
                else:
                    user = uid
                stars = sorted_users[i][1]
                stars = f"{stars} ⭐"
                table.append([stars, user])
            data = tabulate.tabulate(table, tablefmt="presto", colalign=("right",))
            embed = discord.Embed(
                description=f"{title}{box(data, lang='python')}",
                color=discord.Color.random()
            )
            embed.set_thumbnail(url=ctx.guild.icon_url)
            if you:
                embed.set_footer(text=f"Pages {p + 1}/{pages} ｜ {you}")
            else:
                embed.set_footer(text=f"Pages {p + 1}/{pages}")
            embeds.append(embed)
            start += 10
            stop += 10
        if embeds:
            if len(embeds) == 1:
                embed = embeds[0]
                await ctx.send(embed=embed)
            else:
                await menu(ctx, embeds, DEFAULT_CONTROLS)
        else:
            return await ctx.send("No user data yet!")
