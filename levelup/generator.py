from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import requests
import math
import os


# Yoinked from disrank and modified to suit this cog's needs

class Generator:
    def __init__(self):
        self.star = os.path.join(os.path.dirname(__file__), 'assets', 'star.png')
        self.default_bg = os.path.join(os.path.dirname(__file__), 'assets', 'card.png')
        self.online = os.path.join(os.path.dirname(__file__), 'assets', 'online.png')
        self.offline = os.path.join(os.path.dirname(__file__), 'assets', 'offline.png')
        self.idle = os.path.join(os.path.dirname(__file__), 'assets', 'idle.png')
        self.dnd = os.path.join(os.path.dirname(__file__), 'assets', 'dnd.png')
        self.streaming = os.path.join(os.path.dirname(__file__), 'assets', 'streaming.png')
        self.font1 = os.path.join(os.path.dirname(__file__), 'assets', 'font.ttf')
        self.font2 = os.path.join(os.path.dirname(__file__), 'assets', 'font2.ttf')

    def generate_profile(
            self,
            bg_image: str = None,
            profile_image: str = None,
            level: int = 1,
            current_xp: int = 0,
            user_xp: int = 20,
            next_xp: int = 100,
            user_position: int = 1,
            user_name: str = 'NotSpeified#0117',
            user_status: str = 'online',
            color: tuple = (0, 0, 0),
            messages: int = 0,
            voice: int = 0,
            prestige: int = 0,
            stars: int = 0,
    ):
        if not bg_image:
            card = Image.open(self.default_bg).convert("RGBA")
            width, height = card.size
            if width == 900 and height == 220:
                pass
            else:
                x1 = 0
                y1 = 0
                x2 = width
                nh = math.ceil(width * 0.245)
                y2 = 0

                if nh < height:
                    y1 = (height / 2) - 119
                    y2 = nh + y1

                card = card.crop((x1, y1, x2, y2)).resize((900, 240))
        else:
            bg_bytes = BytesIO(requests.get(bg_image).content)
            card = Image.open(bg_bytes).convert("RGBA")

            width, height = card.size
            if width == 900 and height == 220:
                pass
            else:
                x1 = 0
                y1 = 0
                x2 = width
                nh = math.ceil(width * 0.245)
                y2 = 0

                if nh < height:
                    y1 = (height / 2) - 119
                    y2 = nh + y1

                card = card.crop((x1, y1, x2, y2)).resize((900, 240))

        profile_bytes = BytesIO(requests.get(profile_image).content)
        profile = Image.open(profile_bytes)
        profile = profile.convert('RGBA').resize((180, 180))

        if user_status == 'online':
            status = Image.open(self.online)
        elif user_status == 'offline':
            status = Image.open(self.offline)
        elif user_status == 'idle':
            status = Image.open(self.idle)
        elif user_status == 'streaming':
            status = Image.open(self.streaming)
        elif user_status == 'dnd':
            status = Image.open(self.dnd)
        else:  # Eh just make it offline then
            status = Image.open(self.offline)
        status = status.convert("RGBA").resize((40, 40))

        rep_icon = Image.open(self.star)
        rep_icon = rep_icon.convert("RGBA").resize((40, 40))

        profile_pic_holder = Image.new("RGBA", card.size, (255, 255, 255, 0))

        # Mask to crop image
        mask = Image.new("RGBA", card.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((29, 29, 209, 209), fill=(255, 25, 255, 255))

        # Editing stuff here

        # ======== Fonts to use =============
        font_normal = ImageFont.truetype(self.font1, 40)
        font_small = ImageFont.truetype(self.font1, 25)
        font_signa = ImageFont.truetype(self.font2, 25)
        # ======== Colors ========================
        MAINCOLOR = color
        BORDER = (0, 0, 0)

        def get_str(xp):
            return "{:,}".format(xp)

        draw = ImageDraw.Draw(card)
        rank = f"Rank: #{user_position}"
        level = f"Level: {level}"
        exp = f"Exp: {get_str(user_xp)}/{get_str(next_xp)}"
        messages = f"Messages: {messages}"
        voice = f"Voice Minutes: {voice}"
        name = f"{user_name}"
        if prestige:
            name += f" - Prestige {prestige}"
        rep = str(stars)

        # Drawing borders
        draw.text((245, 22), name, BORDER, font=font_normal, stroke_width=1)
        draw.text((245, 95), rank, BORDER, font=font_small, stroke_width=1)
        draw.text((245, 125), level, BORDER, font=font_small, stroke_width=1)
        draw.text((245, 160), exp, BORDER, font=font_small, stroke_width=1)
        # Borders for 2nd column
        draw.text((450, 95), messages, BORDER, font=font_small, stroke_width=1)
        draw.text((450, 125), voice, BORDER, font=font_small, stroke_width=1)
        # Filling text
        draw.text((245, 22), name, MAINCOLOR, font=font_normal)
        draw.text((245, 95), rank, MAINCOLOR, font=font_small)
        draw.text((245, 125), level, MAINCOLOR, font=font_small)
        draw.text((245, 160), exp, MAINCOLOR, font=font_small)
        # Filling text for 2nd column
        draw.text((450, 95), messages, MAINCOLOR, font=font_small)
        draw.text((450, 125), voice, MAINCOLOR, font=font_small)

        draw.text((747, 16), rep, BORDER, font=font_normal, stroke_width=1)
        draw.text((747, 16), rep, MAINCOLOR, font=font_normal)

        # Adding another blank layer for the progress bar
        blank = Image.new("RGBA", card.size, (255, 255, 255, 0))
        blank_draw = ImageDraw.Draw(blank)
        # rectangle 0:x, 1:top y, 2:length, 3:bottom y
        blank_draw.rectangle((240, 200, 750, 215), fill=(255, 255, 255, 0), outline=BORDER)

        xpneed = next_xp - current_xp
        xphave = user_xp - current_xp

        current_percentage = (xphave / xpneed) * 100
        length_of_bar = (current_percentage * 4.9) + 248

        blank_draw.rectangle((248, 203, length_of_bar, 212), fill=MAINCOLOR)
        # Pfp border
        blank_draw.ellipse((20, 20, 218, 218), fill=(255, 255, 255, 0), outline=MAINCOLOR, width=3)

        profile_pic_holder.paste(profile, (29, 29, 209, 209))

        pre = Image.composite(profile_pic_holder, card, mask)
        pre = Image.alpha_composite(pre, blank)

        blank = Image.new("RGBA", pre.size, (255, 255, 255, 0))
        blank.paste(status, (500, 50))

        # Status badge
        # Another blank
        blank = Image.new("RGBA", pre.size, (255, 255, 255, 0))
        blank.paste(status, (169, 169))

        # Add rep star
        blank.paste(rep_icon, (700, 22))

        final = Image.alpha_composite(pre, blank)
        final_bytes = BytesIO()
        final.save(final_bytes, 'png')
        final_bytes.seek(0)
        return final_bytes

    def generate_levelup(
            self,
            bg_image: str = None,
            profile_image: str = None,
            level: int = 1,
            color: tuple = (0, 0, 0),
    ):
        if not bg_image:
            card = Image.open(self.default_bg).convert("RGBA")
            width, height = card.size
            if width == 180 and height == 70:
                pass
            else:
                x1 = 0
                y1 = 0
                x2 = width
                nh = math.ceil(width * 0.24)
                y2 = 0

                if nh < height:
                    y1 = (height / 2) - 119
                    y2 = nh + y1

                card = card.crop((x1, y1, x2, y2)).resize((180, 70))
        else:
            bg_bytes = BytesIO(requests.get(bg_image).content)
            card = Image.open(bg_bytes).convert("RGBA")
            width, height = card.size
            if width == 180 and height == 70:
                pass
            else:
                x1 = 0
                y1 = 0
                x2 = width
                nh = math.ceil(width * 0.26)
                y2 = 0

                if nh < height:
                    y1 = (height / 2) - 100
                    y2 = nh + y1

                card = card.crop((x1, y1, x2, y2)).resize((180, 70))

        profile_bytes = BytesIO(requests.get(profile_image).content)
        profile = Image.open(profile_bytes)
        profile = profile.convert('RGBA').resize((70, 70))

        # Is used as a blank image for mask
        profile_pic_holder = Image.new("RGBA", card.size, (255, 255, 255, 0))

        # Mask to crop profile image
        mask = Image.new("RGBA", card.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        # Profile pic border
        mask_draw.ellipse((1, 1, 69, 69), fill=(255, 25, 255, 255))

        font_normal = ImageFont.truetype(self.font1, 24)

        MAINCOLOR = color
        BORDER = (0, 0, 0)

        draw = ImageDraw.Draw(card)
        level = f"Level {level}"

        # Drawing borders
        draw.text((75, 15), level, BORDER, font=font_normal, stroke_width=1)
        # Filling text
        draw.text((75, 15), level, MAINCOLOR, font=font_normal)

        blank = Image.new("RGBA", card.size, (255, 255, 255, 0))
        profile_pic_holder.paste(profile, (0, 0))

        pre = Image.composite(profile_pic_holder, card, mask)
        pre = Image.alpha_composite(pre, blank)

        final = Image.alpha_composite(pre, blank)
        final_bytes = BytesIO()
        final.save(final_bytes, 'png')
        final_bytes.seek(0)
        return final_bytes
