import discord
from discord.ext import commands
from discord.ui import View, Button
import asyncio
import os
import re
import requests
import json
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from urllib.parse import urlparse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

QUEUE_FILE = "json/music.json"
FFMPEG_PATH = "ffmpeg"

if not os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE, 'w') as f:
        json.dump({}, f)

def load_music():
    with open(QUEUE_FILE, 'r') as f:
        return json.load(f)

def save_music(data):
    with open(QUEUE_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_guild_music(guild_id):
    data = load_music()
    gid = str(guild_id)
    if gid not in data:
        data[gid] = {"queue": [], "repeat_mode": "off"}
        save_music(data)
    return data, gid

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials())

class TrackView(View):
    def __init__(self, bot, ctx, queue, gid):
        super().__init__(timeout=60)
        self.bot = bot
        self.ctx = ctx
        self.queue = queue
        self.page = 0
        self.gid = gid
        self.message = None

    async def interaction_check(self, interaction):
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except:
                pass

    def build_embed(self):
        start = self.page * 10
        end = min(start + 10, len(self.queue))
        desc = ""
        for i, t in enumerate(self.queue[start:end], start=start + 1):
            title = t['title']
            duration = f"{int(t['duration'] // 60)}:{int(t['duration'] % 60):02d}"
            desc += f"{i}. `{title}` [{duration}] - {t['requester']}\n"
        embed = discord.Embed(title=f"🎵 Antrean Musik (Halaman {self.page + 1})", description=desc, color=discord.Color.purple())
        return embed

    async def on_interaction(self, interaction: discord.Interaction):
        cid = interaction.data.get("custom_id")
        if cid == "prev" and self.page > 0:
            self.page -= 1
        elif cid == "next" and (self.page + 1) * 10 < len(self.queue):
            self.page += 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji='⬅️', style=discord.ButtonStyle.secondary, custom_id="prev")
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        await self.on_interaction(interaction)

    @discord.ui.button(emoji='➡️', style=discord.ButtonStyle.primary, custom_id="next")
    async def next_button(self, interaction: discord.Interaction, button: Button):
        await self.on_interaction(interaction)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.now_playing_msg = {}
        self.progress_tasks = {}

    def search_youtube_stream(self, query):
        ydl_opts = {
            'quiet': True,
            'format': 'bestaudio[ext=webm]/bestaudio/best',
            'default_search': 'ytsearch1',
            'noplaylist': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return {
                'title': info.get('title'),
                'stream_url': info.get('url'),
                'webpage_url': info.get('webpage_url'),
                'duration': info.get('duration') or 0
            }

    async def update_progress_bar(self, ctx, track, start_time):
        guild_id = ctx.guild.id
        message = self.now_playing_msg.get(guild_id)
        duration = track.get('duration', 0)
        if not message or duration == 0:
            return

        try:
            while True:
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                if elapsed > duration or not message:
                    break

                progress = int((elapsed / duration) * 10)
                bar = "■" * progress + "□" * (10 - progress)
                embed = discord.Embed(title="🎶 Now Playing", color=discord.Color.blue())
                embed.description = f"[{bar}] {int(elapsed // 60)}:{int(elapsed % 60):02d} / {int(duration // 60)}:{int(duration % 60):02d}\n{track['title']}\nRequested by: {track['requester']}"

                try:
                    await message.edit(embed=embed)
                except discord.NotFound:
                    break
                await asyncio.sleep(10)

            await message.edit(embed=discord.Embed(title="🎶 Now Playing", description="Lagu selesai.", color=discord.Color.green()))
        except asyncio.CancelledError:
            pass

    async def play_next(self, ctx):
        music_data, gid = get_guild_music(ctx.guild.id)
        queue = music_data[gid]["queue"]

        if not queue:
            return await ctx.send("Antrean habis.")

        track = queue.pop(0)
        save_music(music_data)

        source = discord.FFmpegPCMAudio(
            track['url'],
            executable=FFMPEG_PATH,
            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            options='-vn'
        )

        def after_play(err):
            if err:
                print(f"Playback error: {err}")
            fut = asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop)
            try:
                fut.result()
            except:
                pass

        ctx.voice_client.play(source, after=after_play)

        embed = discord.Embed(title="🎶 Now Playing", color=discord.Color.blue())
        embed.description = f"[□□□□□□□□□□] 0:00 / {int(track.get('duration', 0) // 60)}:{int(track.get('duration', 0) % 60):02d}\n{track['title']}\nRequested by: {track['requester']}"

        msg = await ctx.send(embed=embed)
        self.now_playing_msg[ctx.guild.id] = msg
        start_time = datetime.now(timezone.utc)

        task = self.bot.loop.create_task(self.update_progress_bar(ctx, track, start_time))
        self.progress_tasks[ctx.guild.id] = task

    @commands.command(name="play")
    async def play(self, ctx, *, query: str = None):
        if not ctx.author.voice:
            return await ctx.send("Gabung dulu ke voice channel!")

        if not query:
            return await ctx.send("❌ Harap masukkan judul atau url.")

        try:
            if not ctx.voice_client:
                await ctx.author.voice.channel.connect()
        except Exception as e:
            return await ctx.send(f"❌ Gagal terhubung ke voice channel: {e}")

        await ctx.send("Memproses permintaan...")

        parsed = urlparse(query)
        music_data, gid = get_guild_music(ctx.guild.id)

        try:
            if "spotify.com" in parsed.netloc and "playlist" in query:
                playlist_id = query.split("/")[-1].split("?")[0]
                results = sp.playlist_tracks(playlist_id)

                first_track = True
                for item in results['items']:
                    track_info = item['track']
                    if not track_info:
                        continue
                    track_name = track_info['name']
                    artist_name = track_info['artists'][0]['name']
                    search_query = f"{track_name} {artist_name}"

                    result = self.search_youtube_stream(search_query)
                    if not result or not result['stream_url']:
                        continue

                    track = {
                        "title": f"{track_name} / {artist_name}",
                        "url": result['stream_url'],
                        "requester": str(ctx.author),
                        "duration": result.get('duration', 0)
                    }
                    music_data[gid]["queue"].append(track)
                    save_music(music_data)

                    if first_track and (not ctx.voice_client.is_playing()):
                        await self.play_next(ctx)
                        first_track = False

                await ctx.send("🎶 Playlist Spotify sedang dimuat.")

            elif "spotify.com" in parsed.netloc and "track" in query:
                track_id = query.split("/")[-1].split("?")[0]
                track_info = sp.track(track_id)
                track_name = track_info['name']
                artist_name = track_info['artists'][0]['name']
                search_query = f"{track_name} {artist_name}"

                result = self.search_youtube_stream(search_query)
                if not result or not result['stream_url']:
                    return await ctx.send("❌ Tidak bisa stream lagu dari YouTube.")

                track = {
                    "title": f"{track_name} / {artist_name}",
                    "url": result['stream_url'],
                    "requester": str(ctx.author),
                    "duration": result.get('duration', 0)
                }
                music_data[gid]["queue"].append(track)
                save_music(music_data)
                await ctx.send(f"🎶 Ditambahkan dari Spotify track: `{track['title']}`")

                if not ctx.voice_client.is_playing():
                    await self.play_next(ctx)

            else:
                result = self.search_youtube_stream(query)
                if not result or not result['stream_url']:
                    return await ctx.send("❌ Tidak bisa stream lagu dari YouTube.")

                track = {
                    "title": result['title'],
                    "url": result['stream_url'],
                    "requester": str(ctx.author),
                    "duration": result.get('duration', 0)
                }
                music_data[gid]["queue"].append(track)
                save_music(music_data)
                await ctx.send(f"🎶 Ditambahkan dari YouTube: `{track['title']}`")

                if not ctx.voice_client.is_playing():
                    await self.play_next(ctx)

        except Exception as e:
            return await ctx.send(f"❌ Gagal memproses permintaan: {e}")

        if not ctx.voice_client.is_playing():
            await self.play_next(ctx)

    @commands.command(name="skip")
    async def skip(self, ctx):
        if ctx.voice_client and ctx.voice_client.is_playing():
            task = self.progress_tasks.pop(ctx.guild.id, None)
            if task:
                task.cancel()

            msg = self.now_playing_msg.get(ctx.guild.id)
            if msg:
                try:
                    await msg.edit(embed=discord.Embed(
                        title="Skipped",
                        description="Lagu dihentikan oleh pengguna",
                        color=discord.Color.orange()
                    ))
                except discord.NotFound:
                    pass
                self.now_playing_msg.pop(ctx.guild.id, None)

            ctx.voice_client.stop()

            await ctx.send("⏭ Lagu dilewati.")
        else:
            await ctx.send("🚫 Tidak ada lagu yang sedang diputar.")


    @commands.command(name="stop")
    async def stop(self, ctx):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()

        task = self.progress_tasks.pop(ctx.guild.id, None)
        if task:
            task.cancel()

        music_data, gid = get_guild_music(ctx.guild.id)
        music_data[gid]["queue"] = []
        save_music(music_data)

        msg = self.now_playing_msg.pop(ctx.guild.id, None)
        if msg:
            try:
                await msg.edit(embed=discord.Embed(
                    title="🎶 Musik Dihentikan",
                    description="Pemutaran musik dihentikan",
                    color=discord.Color.red()
                ))
            except discord.NotFound:
                pass

        await ctx.send("⏹ Musik dihentikan.")

    @commands.command(name="track")
    async def track(self, ctx):
        music_data, gid = get_guild_music(ctx.guild.id)
        queue = music_data[gid]["queue"]

        if not queue:
            return await ctx.send("🚫 Antrean kosong.")

        view = TrackView(self.bot, ctx, queue, gid)
        view.message = await ctx.send(embed=view.build_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Music(bot))
    