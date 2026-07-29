"""
translations.py
~~~~~~~~~~~~~~~
Single source of truth for user-facing message text in the seven supported
languages: English ('en'), Hindi/Devanagari formal-आप ('hi'), balanced
Hinglish/Roman ('hinglish'), Punjabi/Gurmukhi with a witty, masti tone
('punjabi'), Haryanvi/Devanagari with a cheeky, local-slang tone ('haryanvi'),
Tamil script ('tamil'), and Gujarati script ('gujarati').

Every in-scope string is a key in _T mapping lang -> template. Templates use
str.format named fields (e.g. {name}, {price}) and keep HTML tags + command
tokens (/add) + emoji identical across languages — only the words change.
Bot/technical terms (site names, "URL", "stock", "admin", "plan", "trial",
command names) are deliberately kept in English/Latin script across every
language, mirroring how these terms are actually used in spoken/typed
regional-language tech conversation in India — this matches the existing
hi/hinglish code-switching convention and is intentional, not an oversight.

Resolve text with t(key, lang, **vars). Callers get the recipient's lang from
the DB (database.get_user_lang) — the aiogram handlers, the background stock
alert loop, and the web dashboard's notifier all funnel through here so the
surfaces can't drift.

NOTE: admin-only commands, rare/technical errors, store names, product names,
URLs and numbers are intentionally NOT translated (English/literal).
"""

import logging

logger = logging.getLogger(__name__)

LANGS = ("en", "hi", "hinglish", "punjabi", "haryanvi", "tamil", "gujarati")
DEFAULT_LANG = "en"

LANG_LABEL = {
    "en": "English",
    "hi": "हिंदी",
    "hinglish": "Hinglish",
    "punjabi": "ਪੰਜਾਬੀ",
    "haryanvi": "हरियाणवी",
    "tamil": "தமிழ்",
    "gujarati": "ગુજરાતી",
}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Return the translated, formatted string for `key` in `lang`. Falls back
    to English for an unknown lang/key, and to the raw template if formatting
    fails (missing var) — never raises into a message-send path."""
    table = _T.get(key)
    if table is None:
        logger.warning(f"[i18n] missing translation key: {key!r}")
        return key
    template = table.get(lang) or table.get(DEFAULT_LANG) or ""
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception as exc:
        logger.error(f"[i18n] format failed for key={key!r} lang={lang!r}: {exc}")
        return table.get(DEFAULT_LANG, template)


_T: dict[str, dict[str, str]] = {
    # ── Onboarding / /start ──────────────────────────────────────────────────
    "welcome_body": {
        "en": (
            "👋 <b>Welcome to Ullu Alert!</b>\n\n{trial_line}"
            "I monitor products on multiple online shopping sites "
            "and alert you the moment they come back in stock.\n\n"
        ),
        "hi": (
            "👋 <b>Ullu Alert में आपका स्वागत है!</b>\n\n{trial_line}"
            "मैं कई online shopping sites पर products को monitor करता हूँ और "
            "जैसे ही वो stock में वापस आते हैं, आपको तुरंत alert भेजता हूँ।\n\n"
        ),
        "hinglish": (
            "👋 <b>Ullu Alert me aapka welcome hai!</b>\n\n{trial_line}"
            "Main bohot saari online shopping sites pe products track karta hoon "
            "aur jaise hi wo wapas stock me aate hain, turant alert bhej deta hoon.\n\n"
        ),
        "punjabi": (
            "👋 <b>Ullu Alert 'ch tuhada swagat hai ji!</b>\n\n{trial_line}"
            "Main kayi online shopping sites te products nu 24/7 nazar rakhda haan, te "
            "jiven hi stock wapas aave, tuhanu sabse pehla dassda haan — koi mauka miss nahi! 🦉\n\n"
        ),
        "haryanvi": (
            "👋 <b>Ullu Alert म्ह थारा स्वागत सै भाई!</b>\n\n{trial_line}"
            "मैं भोत सारी online shopping sites पै तेरे products पै नजर राखूं सूं, अर ज्यूं ए "
            "स्टॉक म्ह वापस आया, तन्नै फट से बता दयुँगा — अबकी बार मौका ना चूकैगा! 🦉\n\n"
        ),
        "tamil": (
            "👋 <b>Ullu Alert-க்கு வரவேற்கிறோம்!</b>\n\n{trial_line}"
            "நான் பல online shopping sites-ல் products-ஐ கண்காணித்து, அவை stock-க்கு "
            "திரும்பி வந்தவுடன் உங்களுக்கு உடனே alert அனுப்புவேன்.\n\n"
        ),
        "gujarati": (
            "👋 <b>Ullu Alert માં તમારું સ્વાગત છે!</b>\n\n{trial_line}"
            "હું ઘણી online shopping sites પર products ને monitor કરું છું અને "
            "તે stock માં પાછા આવે કે તરત જ તમને alert મોકલું છું.\n\n"
        ),
    },
    "welcome_commands": {
        "en": (
            "<b>Commands:</b>\n"
            "  /add     – Track product(s); bulk format: <code>Name | URL</code> one per line\n"
            "  /list    – View your tracked products\n"
            "  /remove  – Stop tracking a product\n"
            "  /check   – Check stock (filter by store, or check all at once)\n"
            "  /select  – Select items to bulk-check or delete\n"
            "  /search  – Search your tracked products by name\n"
            "  /stores  – List all supported stores\n"
            "  /pins    – Manage your delivery pin codes\n"
            "  /language – Change language (7 languages available)\n"
            "  /freetrial – Get a bonus free trial by sharing on WhatsApp\n\n"
            "Use /add to get started!"
        ),
        "hi": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track करें; bulk format: <code>Name | URL</code> एक लाइन में एक\n"
            "  /list    – अपने tracked products देखें\n"
            "  /remove  – किसी product को track करना बंद करें\n"
            "  /check   – Stock check करें (store चुनें, या सब एक साथ)\n"
            "  /select  – Bulk check या delete के लिए items चुनें\n"
            "  /search  – अपने products नाम से खोजें\n"
            "  /stores  – सभी supported stores देखें\n"
            "  /pins    – अपने delivery pin codes manage करें\n"
            "  /language – भाषा बदलें (7 भाषाएं उपलब्ध)\n"
            "  /freetrial – WhatsApp पर share करके bonus free trial पाएं\n\n"
            "शुरू करने के लिए /add इस्तेमाल करें!"
        ),
        "hinglish": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track karo; bulk format: <code>Name | URL</code> ek line me ek\n"
            "  /list    – Apne tracked products dekho\n"
            "  /remove  – Kisi product ko track karna band karo\n"
            "  /check   – Stock check karo (store choose karo, ya sab ek saath)\n"
            "  /select  – Bulk check ya delete ke liye items choose karo\n"
            "  /search  – Apne products naam se search karo\n"
            "  /stores  – Saare supported stores dekho\n"
            "  /pins    – Apne delivery pin codes manage karo\n"
            "  /language – Language badlo (7 languages available)\n"
            "  /freetrial – WhatsApp pe share karke bonus free trial pao\n\n"
            "Shuru karne ke liye /add use karo!"
        ),
        "punjabi": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track karo; bulk format: <code>Name | URL</code> ik line 'ch ik\n"
            "  /list    – Apne tracked products vekho\n"
            "  /remove  – Kise product nu track karna band karo\n"
            "  /check   – Stock check karo (store choose karo, ja sare iko vaar)\n"
            "  /select  – Bulk check ja delete layi items choose karo\n"
            "  /search  – Apne products naam naal labho\n"
            "  /stores  – Sare supported stores vekho\n"
            "  /pins    – Apne delivery pin codes manage karo\n"
            "  /language – Boli badlo (7 bolian available ne)\n"
            "  /freetrial – WhatsApp te share karke bonus free trial jitto\n\n"
            "Shuru karan layi /add use karo, chalo lag jao kamm te! 😎"
        ),
        "haryanvi": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track कर; bulk format: <code>Name | URL</code> एक लाइन म्ह एक\n"
            "  /list    – अपणे tracked products देख\n"
            "  /remove  – कोए product ट्रैक करणा बंद कर\n"
            "  /check   – स्टॉक चैक कर (स्टोर छाँट, या सारे एक साथ)\n"
            "  /select  – Bulk check या delete खात्तर items छाँट\n"
            "  /search  – अपणे products नाम तै ढूंढ\n"
            "  /stores  – सारे supported stores देख\n"
            "  /pins    – अपणे delivery pin codes manage कर\n"
            "  /language – भाषा बदल (7 भाषा उपलब्ध सैं)\n"
            "  /freetrial – WhatsApp पै share करके बोनस free trial ले\n\n"
            "चल भाई, शुरू करण खात्तर /add दबा दे!"
        ),
        "tamil": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track செய்யுங்கள்; bulk format: <code>Name | URL</code> ஒரு வரிக்கு ஒன்று\n"
            "  /list    – உங்கள் tracked products பாருங்கள்\n"
            "  /remove  – ஒரு product-ஐ track செய்வதை நிறுத்துங்கள்\n"
            "  /check   – Stock check செய்யுங்கள் (store வாரியாக, அல்லது எல்லாமே ஒரே நேரத்தில்)\n"
            "  /select  – Bulk check அல்லது delete செய்ய items தேர்ந்தெடுங்கள்\n"
            "  /search  – உங்கள் products பெயரால் தேடுங்கள்\n"
            "  /stores  – ஆதரிக்கப்படும் அனைத்து stores பட்டியல்\n"
            "  /pins    – உங்கள் delivery pin codes நிர்வகிக்கவும்\n"
            "  /language – மொழி மாற்றவும் (7 மொழிகள் உள்ளன)\n"
            "  /freetrial – WhatsApp-ல் share செய்து bonus free trial பெறுங்கள்\n\n"
            "தொடங்க /add-ஐ பயன்படுத்துங்கள்!"
        ),
        "gujarati": (
            "<b>Commands:</b>\n"
            "  /add     – Product(s) track કરો; bulk format: <code>Name | URL</code> એક લાઇનમાં એક\n"
            "  /list    – તમારા tracked products જુઓ\n"
            "  /remove  – કોઈ product track કરવાનું બંધ કરો\n"
            "  /check   – Stock check કરો (store પસંદ કરો, અથવા બધા એકસાથે)\n"
            "  /select  – Bulk check અથવા delete માટે items પસંદ કરો\n"
            "  /search  – તમારા products નામ પરથી શોધો\n"
            "  /stores  – બધા supported stores જુઓ\n"
            "  /pins    – તમારા delivery pin codes manage કરો\n"
            "  /language – ભાષા બદલો (7 ભાષાઓ ઉપલબ્ધ)\n"
            "  /freetrial – WhatsApp પર share કરીને bonus free trial મેળવો\n\n"
            "શરૂ કરવા માટે /add નો ઉપયોગ કરો!"
        ),
    },
    "status_trial": {
        "en": "🎁 <b>Free trial active</b> — {days} day(s) left (started with a {trial_days}-day trial).\n\n",
        "hi": "🎁 <b>Free trial चालू है</b> — {days} दिन बाकी ({trial_days}-दिन के trial से शुरू)।\n\n",
        "hinglish": "🎁 <b>Free trial active hai</b> — {days} din bache ({trial_days}-din ke trial se shuru).\n\n",
        "punjabi": "🎁 <b>Free trial chalu hai ji</b> — {days} din bache ne ({trial_days}-din de trial toh shuru hoya si).\n\n",
        "haryanvi": "🎁 <b>Free trial चालू सै भाई</b> — {days} दिन बाकी सैं ({trial_days}-दिन के trial तै शुरू होया था)।\n\n",
        "tamil": "🎁 <b>Free trial செயலில் உள்ளது</b> — {days} நாள்(கள்) மீதம் ({trial_days}-நாள் trial-ஆக தொடங்கியது).\n\n",
        "gujarati": "🎁 <b>Free trial ચાલુ છે</b> — {days} દિવસ બાકી ({trial_days}-દિવસના trial થી શરૂ થયું).\n\n",
    },
    "status_plan": {
        "en": "✅ <b>{plan}</b> active — {days} day(s) left.\n\n",
        "hi": "✅ <b>{plan}</b> चालू है — {days} दिन बाकी।\n\n",
        "hinglish": "✅ <b>{plan}</b> active hai — {days} din bache.\n\n",
        "punjabi": "✅ <b>{plan}</b> chalu hai ji — {days} din bache ne.\n\n",
        "haryanvi": "✅ <b>{plan}</b> चालू सै — {days} दिन बाकी सैं।\n\n",
        "tamil": "✅ <b>{plan}</b> செயலில் உள்ளது — {days} நாள்(கள்) மீதம்.\n\n",
        "gujarati": "✅ <b>{plan}</b> ચાલુ છે — {days} દિવસ બાકી.\n\n",
    },

    # ── Access / lockout ─────────────────────────────────────────────────────
    "access_blocked": {
        "en": ("🚫 <b>Your access has been blocked by the admin.</b>\n\n"
               "If you believe this is a mistake, please contact the admin."),
        "hi": ("🚫 <b>आपका access admin द्वारा block कर दिया गया है।</b>\n\n"
               "अगर आपको लगता है कि ये गलती है, तो admin से संपर्क करें।"),
        "hinglish": ("🚫 <b>Aapka access admin ne block kar diya hai.</b>\n\n"
                     "Agar aapko lagta hai ye galti hai, to admin se contact karo."),
        "punjabi": ("🚫 <b>Tuhada access admin ne block kar dita hai ji.</b>\n\n"
                    "Je tuhanu lagda hai ehh galti hai, tan admin nu sampark karo."),
        "haryanvi": ("🚫 <b>थारा access admin नै block कर दिया सै भाई।</b>\n\n"
                     "जै तन्नै लागे सै के ये गलती सै, तो admin तै बात कर।"),
        "tamil": ("🚫 <b>உங்கள் access-ஐ admin block செய்துவிட்டார்.</b>\n\n"
                  "இது தவறு என்று நினைத்தால், தயவுசெய்து admin-ஐ தொடர்பு கொள்ளுங்கள்."),
        "gujarati": ("🚫 <b>તમારો access admin દ્વારા block કરવામાં આવ્યો છે.</b>\n\n"
                     "જો તમને લાગે કે આ ભૂલ છે, તો કૃપા કરી admin નો સંપર્ક કરો."),
    },
    "access_no_trial": {
        "en": ("👋 <b>You don't have an active trial yet.</b>\n\n"
               "Use /freetrial to get a free trial by sharing Ullu Alert on "
               "WhatsApp, or contact the admin for manual approval."),
        "hi": ("👋 <b>अभी आपके पास कोई active trial नहीं है।</b>\n\n"
               "WhatsApp पर Ullu Alert share करके free trial पाने के लिए /freetrial "
               "इस्तेमाल करें, या manual approval के लिए admin से संपर्क करें।"),
        "hinglish": ("👋 <b>Abhi aapke paas koi active trial nahi hai.</b>\n\n"
                     "WhatsApp pe Ullu Alert share karke free trial paane ke liye /freetrial "
                     "use karo, ya manual approval ke liye admin se contact karo."),
        "punjabi": ("👋 <b>Abhi tuhade kol koi active trial nahi hai ji.</b>\n\n"
                    "WhatsApp te Ullu Alert share karke free trial layi /freetrial "
                    "use karo, ja manual approval layi admin nu sampark karo."),
        "haryanvi": ("👋 <b>अभी तेरे पाच कोए active trial कोनी भाई।</b>\n\n"
                     "WhatsApp पै Ullu Alert शेयर करके free trial पाण खात्तर /freetrial "
                     "दबा, या फेर admin तै मिलकै approval ले ले।"),
        "tamil": ("👋 <b>தற்போது உங்களுக்கு active trial இல்லை.</b>\n\n"
                  "WhatsApp-ல் Ullu Alert-ஐ share செய்து free trial பெற /freetrial-ஐ "
                  "பயன்படுத்துங்கள், அல்லது manual approval-க்கு admin-ஐ தொடர்பு கொள்ளுங்கள்."),
        "gujarati": ("👋 <b>અત્યારે તમારી પાસે કોઈ active trial નથી.</b>\n\n"
                     "WhatsApp પર Ullu Alert share કરીને free trial મેળવવા /freetrial "
                     "નો ઉપયોગ કરો, અથવા manual approval માટે admin નો સંપર્ક કરો."),
    },
    "access_expired_grace": {
        "en": ("⏰ <b>Your access has expired.</b>\n\n"
               "Your tracked items are safely kept for <b>{grace} more day{s}</b> — "
               "renew within that window and your full list is restored automatically. "
               "After that, they're permanently deleted.\n\n{payment}"),
        "hi": ("⏰ <b>आपका access expire हो गया है।</b>\n\n"
               "आपके tracked items <b>{grace} और दिन</b> तक safe रखे जाते हैं — इस दौरान "
               "renew करें और आपकी पूरी list अपने आप वापस आ जाएगी। उसके बाद वो हमेशा के लिए "
               "delete हो जाते हैं।\n\n{payment}"),
        "hinglish": ("⏰ <b>Aapka access expire ho gaya hai.</b>\n\n"
                     "Aapke tracked items <b>{grace} aur din</b> tak safe rahenge — is beech "
                     "renew karo aur aapki poori list apne aap wapas aa jayegi. Uske baad wo "
                     "hamesha ke liye delete ho jaate hain.\n\n{payment}"),
        "punjabi": ("⏰ <b>Tuhada access expire ho gaya hai ji.</b>\n\n"
                    "Tuhade tracked items <b>{grace} hor din</b> tak safe rahinge — es vich "
                    "renew karo te tuhadi puri list aap hi wapas aa javegi. Us tou baad ohh hamesha "
                    "layi delete ho jaan ge.\n\n{payment}"),
        "haryanvi": ("⏰ <b>थारा access खतम होग्या सै।</b>\n\n"
                     "थारे tracked items <b>{grace} अर दिन</b> ताहीं सम्भाल के राखे सैं — इस बिचाल्ले "
                     "renew कर दे, अर थारी पूरी लिस्ट अपने आप वापस आज्यागी। बाद म्ह वो सारी हमेशा खात्तर "
                     "मिट जावैगी।\n\n{payment}"),
        "tamil": ("⏰ <b>உங்கள் access காலாவதியாகிவிட்டது.</b>\n\n"
                  "உங்கள் tracked items <b>இன்னும் {grace} நாள்(கள்)</b> பாதுகாப்பாக வைக்கப்படும் — "
                  "இந்த நேரத்தில் renew செய்தால் உங்கள் முழு பட்டியலும் தானாக மீட்கப்படும். அதற்குப் பின், "
                  "அவை நிரந்தரமாக நீக்கப்படும்.\n\n{payment}"),
        "gujarati": ("⏰ <b>તમારો access expire થઈ ગયો છે.</b>\n\n"
                     "તમારા tracked items <b>વધુ {grace} દિવસ</b> સુધી સુરક્ષિત રાખવામાં આવે છે — આ "
                     "સમયમાં renew કરો અને તમારી આખી list આપોઆપ પાછી આવી જશે. ત્યાર પછી તે "
                     "કાયમ માટે delete થઈ જશે.\n\n{payment}"),
    },
    "access_trial_ended": {
        "en": ("⏰ <b>Your trial has ended.</b>\n\n"
               "You need an active plan to keep using this bot.\n\n{payment}"),
        "hi": ("⏰ <b>आपका trial खत्म हो गया है।</b>\n\n"
               "इस bot को इस्तेमाल करते रहने के लिए आपको active plan चाहिए।\n\n{payment}"),
        "hinglish": ("⏰ <b>Aapka trial khatam ho gaya hai.</b>\n\n"
                     "Is bot ko use karte rehne ke liye aapko active plan chahiye.\n\n{payment}"),
        "punjabi": ("⏰ <b>Tuhada trial khatam ho gaya hai ji.</b>\n\n"
                    "Es bot nu use karde rehen layi tuhanu active plan chahida hai.\n\n{payment}"),
        "haryanvi": ("⏰ <b>थारा trial खतम होग्या सै।</b>\n\n"
                     "इस बोट नै चलाते रहण खात्तर थारे धोरै active plan होणा जरूरी सै।\n\n{payment}"),
        "tamil": ("⏰ <b>உங்கள் trial முடிந்துவிட்டது.</b>\n\n"
                  "இந்த bot-ஐ தொடர்ந்து பயன்படுத்த உங்களுக்கு active plan தேவை.\n\n{payment}"),
        "gujarati": ("⏰ <b>તમારો trial પૂરો થઈ ગયો છે.</b>\n\n"
                     "આ bot નો ઉપયોગ ચાલુ રાખવા માટે તમારે active plan જોઈએ.\n\n{payment}"),
    },
    "payment_instructions": {
        "en": ("💳 <b>To get access:</b>\n"
               "Send an Amazon Gift Card to the admin (details to be shared) and "
               "include your Telegram user ID in the message.\n\n"
               "📩 Contact: the admin will review and approve your access shortly "
               "after payment — use /start any time to check your status."),
        "hi": ("💳 <b>Access पाने के लिए:</b>\n"
               "Admin को Amazon Gift Card भेजें (details बाद में share होंगी) और message में "
               "अपना Telegram user ID ज़रूर लिखें।\n\n"
               "📩 Contact: payment के बाद admin आपके access को review करके जल्दी approve कर "
               "देंगे — अपना status देखने के लिए कभी भी /start इस्तेमाल करें।"),
        "hinglish": ("💳 <b>Access paane ke liye:</b>\n"
                     "Admin ko Amazon Gift Card bhejo (details baad me share hongi) aur message me "
                     "apna Telegram user ID zaroor likho.\n\n"
                     "📩 Contact: payment ke baad admin aapke access ko review karke jaldi approve "
                     "kar denge — apna status dekhne ke liye kabhi bhi /start use karo."),
        "punjabi": ("💳 <b>Access lain layi:</b>\n"
                    "Admin nu Amazon Gift Card bhejo (details baad vich share hongiyan) te message vich "
                    "apna Telegram user ID zaroor likho ji.\n\n"
                    "📩 Contact: payment tou baad admin tuhade access nu review karke jaldi approve "
                    "kar denge — apna status vekhan layi kadi vi /start use karo."),
        "haryanvi": ("💳 <b>Access पाण खात्तर:</b>\n"
                     "Admin नै Amazon Gift Card भेज दे (details बाद म्ह मिलैगी) अर message म्ह "
                     "अपणा Telegram user ID जरूर लिख दिए।\n\n"
                     "📩 Contact: payment के बाद admin थारे access नै जांच के फट approve कर "
                     "देवैगा — अपणा status देखण खात्तर कदे भी /start दबा दे।"),
        "tamil": ("💳 <b>Access பெற:</b>\n"
                  "Admin-க்கு ஒரு Amazon Gift Card அனுப்புங்கள் (விவரங்கள் பின்னர் பகிரப்படும்) மற்றும் "
                  "message-ல் உங்கள் Telegram user ID-ஐ சேர்க்கவும்.\n\n"
                  "📩 Contact: payment-க்குப் பிறகு admin உங்கள் access-ஐ review செய்து விரைவில் approve "
                  "செய்வார் — உங்கள் status பார்க்க எப்போது வேண்டுமானாலும் /start பயன்படுத்தவும்."),
        "gujarati": ("💳 <b>Access મેળવવા માટે:</b>\n"
                     "Admin ને Amazon Gift Card મોકલો (વિગતો પછી share કરવામાં આવશે) અને message માં "
                     "તમારો Telegram user ID જરૂર લખો.\n\n"
                     "📩 Contact: payment પછી admin તમારો access review કરીને જલ્દી approve "
                     "કરી દેશે — તમારો status જોવા માટે ગમે ત્યારે /start નો ઉપયોગ કરો."),
    },
    "no_active_plan": {
        "en": "⚠️ You don't have an active plan assigned. Contact the admin to get set up.",
        "hi": "⚠️ आपको कोई active plan assign नहीं है। Setup के लिए admin से संपर्क करें।",
        "hinglish": "⚠️ Aapko koi active plan assign nahi hai. Setup ke liye admin se contact karo.",
        "punjabi": "⚠️ Tuhanu koi active plan assign nahi hai ji. Setup layi admin nu sampark karo.",
        "haryanvi": "⚠️ थारे कोए active plan assign कोनी होया। Setup खात्तर admin तै बात कर।",
        "tamil": "⚠️ உங்களுக்கு active plan assign செய்யப்படவில்லை. Setup செய்ய admin-ஐ தொடர்பு கொள்ளுங்கள்.",
        "gujarati": "⚠️ તમને કોઈ active plan assign નથી થયો. Setup માટે admin નો સંપર્ક કરો.",
    },
    "item_limit": {
        "en": ("🚫 <b>Item limit reached.</b>\n\n"
               "Your <b>{plan}</b> plan allows up to <b>{max}</b> tracked items, and "
               "you're currently tracking <b>{count}</b>.\n\n"
               "Remove an item with /remove, or contact the admin to upgrade your plan."),
        "hi": ("🚫 <b>Item limit पूरी हो गई।</b>\n\n"
               "आपका <b>{plan}</b> plan ज़्यादा से ज़्यादा <b>{max}</b> items track करने देता है, "
               "और अभी आप <b>{count}</b> track कर रहे हैं।\n\n"
               "/remove से कोई item हटाएं, या plan upgrade करने के लिए admin से संपर्क करें।"),
        "hinglish": ("🚫 <b>Item limit poori ho gayi.</b>\n\n"
                     "Aapka <b>{plan}</b> plan max <b>{max}</b> items track karne deta hai, aur "
                     "abhi aap <b>{count}</b> track kar rahe ho.\n\n"
                     "/remove se koi item hatao, ya plan upgrade karne ke liye admin se contact karo."),
        "punjabi": ("🚫 <b>Item limit puri ho gayi ji.</b>\n\n"
                    "Tuhada <b>{plan}</b> plan vadh to vadh <b>{max}</b> items track karan dinda hai, "
                    "te abhi tusi <b>{count}</b> track kar rahe ho.\n\n"
                    "/remove naal koi item hatao, ja plan upgrade karan layi admin nu sampark karo."),
        "haryanvi": ("🚫 <b>Item limit पूरी होगी।</b>\n\n"
                     "थारा <b>{plan}</b> plan ज्यादा तै ज्यादा <b>{max}</b> items ट्रैक करण दे सै, "
                     "अर अबार तू <b>{count}</b> ट्रैक कर रहया सै।\n\n"
                     "/remove तै कोए item हटा दे, या फेर plan upgrade खात्तर admin तै बात कर।"),
        "tamil": ("🚫 <b>Item limit முடிந்துவிட்டது.</b>\n\n"
                  "உங்கள் <b>{plan}</b> plan அதிகபட்சம் <b>{max}</b> items track செய்ய அனுமதிக்கிறது, "
                  "தற்போது நீங்கள் <b>{count}</b> track செய்கிறீர்கள்.\n\n"
                  "/remove மூலம் ஒரு item-ஐ நீக்கவும், அல்லது plan-ஐ upgrade செய்ய admin-ஐ தொடர்பு கொள்ளுங்கள்."),
        "gujarati": ("🚫 <b>Item limit પૂરી થઈ ગઈ.</b>\n\n"
                     "તમારો <b>{plan}</b> plan વધુમાં વધુ <b>{max}</b> items track કરવાની છૂટ આપે છે, "
                     "અને અત્યારે તમે <b>{count}</b> track કરી રહ્યા છો.\n\n"
                     "/remove થી કોઈ item હટાવો, અથવા plan upgrade કરવા admin નો સંપર્ક કરો."),
    },
    "store_not_in_plan": {
        "en": ("🚫 <b>Store not included in your plan.</b>\n\n"
               "Your <b>{plan}</b> plan only allows: <b>{sites}</b>.\n\n"
               "Contact the admin to upgrade to a plan that includes this store."),
        "hi": ("🚫 <b>ये store आपके plan में नहीं है।</b>\n\n"
               "आपका <b>{plan}</b> plan सिर्फ़ ये allow करता है: <b>{sites}</b>.\n\n"
               "इस store वाले plan के लिए admin से upgrade करवाएं।"),
        "hinglish": ("🚫 <b>Ye store aapke plan me nahi hai.</b>\n\n"
                     "Aapka <b>{plan}</b> plan sirf ye allow karta hai: <b>{sites}</b>.\n\n"
                     "Is store wale plan ke liye admin se upgrade karwao."),
        "punjabi": ("🚫 <b>Eh store tuhade plan vich nahi hai ji.</b>\n\n"
                    "Tuhada <b>{plan}</b> plan sirf eh allow karda hai: <b>{sites}</b>.\n\n"
                    "Es store wale plan layi admin kolo upgrade karwao."),
        "haryanvi": ("🚫 <b>ये स्टोर थारे plan म्ह कोनी।</b>\n\n"
                     "थारा <b>{plan}</b> plan बस याई allow करै सै: <b>{sites}</b>.\n\n"
                     "इस स्टोर आळे plan खात्तर admin तै upgrade करवा ले।"),
        "tamil": ("🚫 <b>இந்த store உங்கள் plan-ல் இல்லை.</b>\n\n"
                  "உங்கள் <b>{plan}</b> plan அனுமதிப்பது: <b>{sites}</b> மட்டுமே.\n\n"
                  "இந்த store உள்ள plan-க்கு admin மூலம் upgrade செய்யுங்கள்."),
        "gujarati": ("🚫 <b>આ store તમારા plan માં નથી.</b>\n\n"
                     "તમારો <b>{plan}</b> plan ફક્ત આ allow કરે છે: <b>{sites}</b>.\n\n"
                     "આ store વાળા plan માટે admin પાસેથી upgrade કરાવો."),
    },
    "store_locked": {
        "en": ("🔒 <b>{site} tracking is currently unavailable.</b>\n\n"
               "This store has been temporarily disabled. Please try another "
               "store, or check back later."),
        "hi": ("🔒 <b>{site} tracking अभी उपलब्ध नहीं है।</b>\n\n"
               "ये store फ़िलहाल disable किया गया है। कृपया कोई और store आज़माएं, "
               "या बाद में दोबारा देखें।"),
        "hinglish": ("🔒 <b>{site} tracking abhi available nahi hai.</b>\n\n"
                     "Ye store filhaal disable kiya gaya hai. Koi aur store try karo, "
                     "ya baad me dobara check karo."),
        "punjabi": ("🔒 <b>{site} tracking abhi available nahi hai ji.</b>\n\n"
                    "Eh store filhaal disable kita gaya hai. Koi hor store try karo, "
                    "ja baad vich dobara check karo."),
        "haryanvi": ("🔒 <b>{site} tracking अबार उपलब्ध कोनी।</b>\n\n"
                     "ये स्टोर फिलहाल बंद करया सै। कोए और स्टोर देख ले, "
                     "या फेर बाद म्ह दोबारा चैक कर।"),
        "tamil": ("🔒 <b>{site} tracking தற்போது கிடைக்கவில்லை.</b>\n\n"
                  "இந்த store தற்காலிகமாக disable செய்யப்பட்டுள்ளது. வேறு "
                  "store-ஐ முயற்சிக்கவும், அல்லது பின்னர் மீண்டும் check செய்யவும்."),
        "gujarati": ("🔒 <b>{site} tracking હાલમાં ઉપલબ્ધ નથી.</b>\n\n"
                     "આ store હાલ પૂરતું disable કરવામાં આવ્યું છે. કૃપા કરી બીજું "
                     "store અજમાવો, અથવા પછી ફરી check કરો."),
    },

    # ── Notifications ────────────────────────────────────────────────────────
    "stock_alert": {
        "en": ("🚨 <b>Back in Stock!</b>\n\n"
               "📦 <b>{name}</b> is now available on <b>{site}</b>!{price_line}\n\n"
               "🛒 <a href=\"{url}\">Buy it now →</a>"),
        "hi": ("🚨 <b>वापस स्टॉक में!</b>\n\n"
               "📦 <b>{name}</b> अब <b>{site}</b> पर उपलब्ध है!{price_line}\n\n"
               "🛒 <a href=\"{url}\">अभी खरीदें →</a>"),
        "hinglish": ("🚨 <b>Wapas Stock me aa gaya!</b>\n\n"
                     "📦 <b>{name}</b> ab <b>{site}</b> pe available hai!{price_line}\n\n"
                     "🛒 <a href=\"{url}\">Abhi kharido →</a>"),
        "punjabi": ("🚨 <b>Oye hoi hoi! Stock wapas aa gaya!</b>\n\n"
                    "📦 <b>{name}</b> hun <b>{site}</b> te available hai ji!{price_line}\n\n"
                    "🛒 <a href=\"{url}\">Turant kharido, mauka na gawao →</a>"),
        "haryanvi": ("🚨 <b>अरे भाई! स्टॉक म्ह आग्या!</b>\n\n"
                     "📦 <b>{name}</b> अब <b>{site}</b> पै मिलण लाग्या सै!{price_line}\n\n"
                     "🛒 <a href=\"{url}\">फट खरीद ले, मौका ना जावै →</a>"),
        "tamil": ("🚨 <b>மீண்டும் Stock-ல் உள்ளது!</b>\n\n"
                  "📦 <b>{name}</b> இப்போது <b>{site}</b>-ல் கிடைக்கிறது!{price_line}\n\n"
                  "🛒 <a href=\"{url}\">இப்போதே வாங்குங்கள் →</a>"),
        "gujarati": ("🚨 <b>પાછું Stock માં આવ્યું!</b>\n\n"
                     "📦 <b>{name}</b> હવે <b>{site}</b> પર ઉપલબ્ધ છે!{price_line}\n\n"
                     "🛒 <a href=\"{url}\">હમણાં જ ખરીદો →</a>"),
    },
    "stock_alert_price_line": {
        "en": "\n💰 <b>Current price: ₹{price}</b>",
        "hi": "\n💰 <b>अभी कीमत: ₹{price}</b>",
        "hinglish": "\n💰 <b>Abhi ka price: ₹{price}</b>",
        "punjabi": "\n💰 <b>Hun da price: ₹{price}</b>",
        "haryanvi": "\n💰 <b>अबार का प्राइस: ₹{price}</b>",
        "tamil": "\n💰 <b>தற்போதைய price: ₹{price}</b>",
        "gujarati": "\n💰 <b>હાલની price: ₹{price}</b>",
    },
    "item_removed_tail": {
        "en": ("To keep the bot fast, accurate, and running smoothly for everyone, "
               "some items get cleared. Re-add anytime with /add!"),
        "hi": ("Bot को सबके लिए fast, सही और smooth रखने के लिए कुछ items हटा दिए जाते हैं। "
               "कभी भी /add से दोबारा जोड़ सकते हैं!"),
        "hinglish": ("Bot ko sabke liye fast, accurate aur smooth rakhne ke liye kuch items "
                     "clear ho jaate hain. Kabhi bhi /add se dobara add kar sakte ho!"),
        "punjabi": ("Bot nu sabke layi fast, accurate te smooth rakhan layi kuch items "
                    "clear kar dite jaande ne. Kadi vi /add naal dobara add kar sakde ho ji!"),
        "haryanvi": ("बोट नै सबकै खात्तर fast, सही अर smooth राखण खात्तर कुछ items हटा दिए जावै सैं। "
                     "कदे भी /add तै दोबारा जोड़ ले!"),
        "tamil": ("Bot-ஐ அனைவருக்கும் fast, accurate மற்றும் smooth ஆக வைத்திருக்க சில items "
                  "நீக்கப்படும். எப்போது வேண்டுமானாலும் /add மூலம் மீண்டும் சேர்க்கலாம்!"),
        "gujarati": ("Bot ને બધા માટે fast, accurate અને smooth રાખવા માટે કેટલાક items "
                     "દૂર કરવામાં આવે છે. ગમે ત્યારે /add થી ફરીથી ઉમેરી શકો છો!"),
    },
    "item_removed_single": {
        "en": "🦉 Ullu removed: {name}",
        "hi": "🦉 Ullu ने हटाया: {name}",
        "hinglish": "🦉 Ullu ne hataya: {name}",
        "punjabi": "🦉 Ullu ne hataya: {name}",
        "haryanvi": "🦉 Ullu नै हटाया: {name}",
        "tamil": "🦉 Ullu நீக்கியது: {name}",
        "gujarati": "🦉 Ulluએ દૂર કર્યું: {name}",
    },
    "item_removed_multi_header": {
        "en": "🦉 Ullu removed the following items:",
        "hi": "🦉 Ullu ने ये items हटाए:",
        "hinglish": "🦉 Ullu ne ye items hataye:",
        "punjabi": "🦉 Ullu ne eh items hataye:",
        "haryanvi": "🦉 Ullu नै ये items हटाए:",
        "tamil": "🦉 Ullu பின்வரும் items-ஐ நீக்கியது:",
        "gujarati": "🦉 Ulluએ નીચેના items દૂર કર્યા:",
    },
    "approval_notice": {
        "en": ("✅ <b>Access approved!</b>\n\n"
               "📦 Plan: <b>{plan}</b>\n➕ Days added: <b>{days}</b>\n"
               "📅 Access until: <b>{until}</b>\n\n"
               "Thanks for your payment — you're all set. Use /list to see your tracked items."),
        "hi": ("✅ <b>Access approve हो गया!</b>\n\n"
               "📦 Plan: <b>{plan}</b>\n➕ जोड़े गए दिन: <b>{days}</b>\n"
               "📅 Access तब तक: <b>{until}</b>\n\n"
               "आपके payment के लिए धन्यवाद — अब सब तैयार है। अपने tracked items देखने के लिए /list इस्तेमाल करें।"),
        "hinglish": ("✅ <b>Access approve ho gaya!</b>\n\n"
                     "📦 Plan: <b>{plan}</b>\n➕ Din add hue: <b>{days}</b>\n"
                     "📅 Access kab tak: <b>{until}</b>\n\n"
                     "Payment ke liye thanks — ab sab set hai. Apne tracked items dekhne ke liye /list use karo."),
        "punjabi": ("✅ <b>Access approve ho gaya ji, mubarakan!</b>\n\n"
                    "📦 Plan: <b>{plan}</b>\n➕ Din add hoye: <b>{days}</b>\n"
                    "📅 Access kadon tak: <b>{until}</b>\n\n"
                    "Payment layi tuhada dhanwaad — hun sab set hai. Apne tracked items vekhan layi /list use karo."),
        "haryanvi": ("✅ <b>Access approve होग्या, बधाई हो भाई!</b>\n\n"
                     "📦 Plan: <b>{plan}</b>\n➕ जोड़े गए दिन: <b>{days}</b>\n"
                     "📅 Access कद ताहीं: <b>{until}</b>\n\n"
                     "payment खात्तर धन्यवाद — अब सारा सेट सै। अपणे tracked items देखण खात्तर /list दबा दे।"),
        "tamil": ("✅ <b>Access approve ஆகிவிட்டது!</b>\n\n"
                  "📦 Plan: <b>{plan}</b>\n➕ சேர்க்கப்பட்ட நாட்கள்: <b>{days}</b>\n"
                  "📅 Access இது வரை: <b>{until}</b>\n\n"
                  "உங்கள் payment-க்கு நன்றி — எல்லாம் தயார். உங்கள் tracked items பார்க்க /list பயன்படுத்துங்கள்."),
        "gujarati": ("✅ <b>Access approve થઈ ગયો!</b>\n\n"
                     "📦 Plan: <b>{plan}</b>\n➕ ઉમેરાયેલા દિવસો: <b>{days}</b>\n"
                     "📅 Access ક્યાં સુધી: <b>{until}</b>\n\n"
                     "તમારા payment માટે આભાર — હવે બધું તૈયાર છે. તમારા tracked items જોવા /list નો ઉપયોગ કરો."),
    },
    "rejection_notice": {
        "en": "❌ <b>Your access request was not approved.</b>{reason}\n\nContact the admin if you have questions.",
        "hi": "❌ <b>आपका access request approve नहीं हुआ।</b>{reason}\n\nकोई सवाल हो तो admin से संपर्क करें।",
        "hinglish": "❌ <b>Aapka access request approve nahi hua.</b>{reason}\n\nKoi sawaal ho to admin se contact karo.",
        "punjabi": "❌ <b>Tuhada access request approve nahi hoya ji.</b>{reason}\n\nKoi sawaal hove tan admin nu sampark karo.",
        "haryanvi": "❌ <b>थारा access request approve कोनी होया।</b>{reason}\n\nकोए सवाल हो तो admin तै बात कर ले।",
        "tamil": "❌ <b>உங்கள் access request approve செய்யப்படவில்லை.</b>{reason}\n\nஏதேனும் கேள்வி இருந்தால் admin-ஐ தொடர்பு கொள்ளுங்கள்.",
        "gujarati": "❌ <b>તમારો access request approve થયો નથી.</b>{reason}\n\nકોઈ પ્રશ્ન હોય તો admin નો સંપર્ક કરો.",
    },
    "rejection_reason": {
        "en": "\n\nReason: {reason}",
        "hi": "\n\nकारण: {reason}",
        "hinglish": "\n\nReason: {reason}",
        "punjabi": "\n\nKaaran: {reason}",
        "haryanvi": "\n\nकारण: {reason}",
        "tamil": "\n\nகாரணம்: {reason}",
        "gujarati": "\n\nકારણ: {reason}",
    },
    "block_notice": {
        "en": ("🚫 <b>Your access has been blocked by the admin.</b>\n\n"
               "Contact the admin if you believe this is a mistake."),
        "hi": ("🚫 <b>आपका access admin द्वारा block कर दिया गया है।</b>\n\n"
               "अगर आपको लगता है कि ये गलती है, तो admin से संपर्क करें।"),
        "hinglish": ("🚫 <b>Aapka access admin ne block kar diya hai.</b>\n\n"
                     "Agar aapko lagta hai ye galti hai, to admin se contact karo."),
        "punjabi": ("🚫 <b>Tuhada access admin ne block kar dita hai ji.</b>\n\n"
                    "Je tuhanu lagda hai ehh galti hai, tan admin nu sampark karo."),
        "haryanvi": ("🚫 <b>थारा access admin नै block कर दिया सै भाई।</b>\n\n"
                     "जै तन्नै लागे सै के ये गलती सै, तो admin तै बात कर।"),
        "tamil": ("🚫 <b>உங்கள் access-ஐ admin block செய்துவிட்டார்.</b>\n\n"
                  "இது தவறு என்று நினைத்தால், admin-ஐ தொடர்பு கொள்ளுங்கள்."),
        "gujarati": ("🚫 <b>તમારો access admin દ્વારા block કરવામાં આવ્યો છે.</b>\n\n"
                     "જો તમને લાગે કે આ ભૂલ છે, તો admin નો સંપર્ક કરો."),
    },
    "unblock_notice": {
        "en": "✅ <b>Your access has been restored.</b> Welcome back!",
        "hi": "✅ <b>आपका access वापस चालू कर दिया गया है।</b> वापसी पर स्वागत है!",
        "hinglish": "✅ <b>Aapka access wapas chalu kar diya gaya hai.</b> Welcome back!",
        "punjabi": "✅ <b>Tuhada access wapas chalu kar dita gaya hai ji.</b> Wapsi te khushamdeed!",
        "haryanvi": "✅ <b>थारा access वापस चालू करदिया सै।</b> आजा भाई, फेर तै स्वागत सै!",
        "tamil": "✅ <b>உங்கள் access மீண்டும் இயக்கப்பட்டது.</b> மீண்டும் வரவேற்கிறோம்!",
        "gujarati": "✅ <b>તમારો access પાછો ચાલુ કરવામાં આવ્યો છે.</b> પાછા સ્વાગત છે!",
    },
    "plan_cancelled_notice": {
        "en": ("🚫 <b>Your plan has been cancelled by the admin.</b>\n\n"
               "Your tracked items are safe, but alerts have stopped. "
               "Contact the admin to renew your plan."),
        "hi": ("🚫 <b>आपका plan admin द्वारा cancel कर दिया गया है।</b>\n\n"
               "आपके tracked items safe हैं, पर alerts बंद हो गए हैं। "
               "Plan renew करने के लिए admin से संपर्क करें।"),
        "hinglish": ("🚫 <b>Aapka plan admin ne cancel kar diya hai.</b>\n\n"
                     "Aapke tracked items safe hain, par alerts band ho gaye hain. "
                     "Plan renew karne ke liye admin se contact karo."),
        "punjabi": ("🚫 <b>Tuhada plan admin ne cancel kar dita hai ji.</b>\n\n"
                    "Tuhade tracked items safe ne, par alerts band ho gaye ne. "
                    "Plan renew karan layi admin nu sampark karo."),
        "haryanvi": ("🚫 <b>थारा plan admin नै cancel कर दिया सै।</b>\n\n"
                     "थारे tracked items सुरक्षित सै, पर alerts बंद होगे सै। "
                     "Plan renew करण खात्तर admin तै बात कर।"),
        "tamil": ("🚫 <b>உங்கள் plan-ஐ admin cancel செய்துவிட்டார்.</b>\n\n"
                  "உங்கள் tracked items பாதுகாப்பாக உள்ளன, ஆனால் alerts நிறுத்தப்பட்டுள்ளன. "
                  "Plan-ஐ renew செய்ய admin-ஐ தொடர்பு கொள்ளுங்கள்."),
        "gujarati": ("🚫 <b>તમારો plan admin દ્વારા cancel કરવામાં આવ્યો છે.</b>\n\n"
                     "તમારી tracked items સુરક્ષિત છે, પણ alerts બંધ થઈ ગયા છે. "
                     "Plan renew કરવા માટે admin નો સંપર્ક કરો."),
    },
    "expiry_reminder": {
        "en": ("⏰ <b>Your {kind} expires in about {hours} hour(s).</b>\n\n"
               "💳 To keep your alerts running, send an Amazon Gift Card to the admin "
               "(details to be shared) and include your Telegram user ID.\n\n"
               "📩 The admin will review and extend your access after payment."),
        "hi": ("⏰ <b>आपका {kind} करीब {hours} घंटे में expire हो जाएगा।</b>\n\n"
               "💳 अपने alerts चालू रखने के लिए admin को Amazon Gift Card भेजें "
               "(details बाद में) और अपना Telegram user ID ज़रूर लिखें।\n\n"
               "📩 Payment के बाद admin आपका access review करके बढ़ा देंगे।"),
        "hinglish": ("⏰ <b>Aapka {kind} karib {hours} ghante me expire ho jayega.</b>\n\n"
                     "💳 Apne alerts chalu rakhne ke liye admin ko Amazon Gift Card bhejo "
                     "(details baad me) aur apna Telegram user ID zaroor likho.\n\n"
                     "📩 Payment ke baad admin aapka access review karke badha denge."),
        "punjabi": ("⏰ <b>Tuhada {kind} takriban {hours} ghantiyan vich khatam ho javega ji.</b>\n\n"
                    "💳 Apne alerts chaalu rakhan layi admin nu Amazon Gift Card bhejo "
                    "(details baad vich) te apna Telegram user ID zaroor likho.\n\n"
                    "📩 Payment tou baad admin tuhada access review karke vadha denge."),
        "haryanvi": ("⏰ <b>थारा {kind} करीबन {hours} घंटे म्ह खतम हो ज्यागा।</b>\n\n"
                     "💳 अपणे alerts चालू राखण खात्तर admin नै Amazon Gift Card भेज दे "
                     "(details बाद म्ह) अर अपणा Telegram user ID जरूर लिख दिए।\n\n"
                     "📩 payment के बाद admin थारा access देख के बढ़ा देवैगा।"),
        "tamil": ("⏰ <b>உங்கள் {kind} கிட்டத்தட்ட {hours} மணி நேரத்தில் காலாவதியாகும்.</b>\n\n"
                  "💳 உங்கள் alerts தொடர, admin-க்கு ஒரு Amazon Gift Card அனுப்புங்கள் "
                  "(விவரங்கள் பின்னர்) மற்றும் உங்கள் Telegram user ID-ஐ சேர்க்கவும்.\n\n"
                  "📩 payment-க்குப் பிறகு admin உங்கள் access-ஐ review செய்து நீட்டிப்பார்."),
        "gujarati": ("⏰ <b>તમારો {kind} લગભગ {hours} કલાકમાં expire થઈ જશે.</b>\n\n"
                     "💳 તમારા alerts ચાલુ રાખવા માટે admin ને Amazon Gift Card મોકલો "
                     "(વિગતો પછી) અને તમારો Telegram user ID જરૂર લખો.\n\n"
                     "📩 payment પછી admin તમારો access review કરીને વધારી દેશે."),
    },
    "expiry_kind_trial": {
        "en": "trial", "hi": "trial", "hinglish": "trial",
        "punjabi": "trial", "haryanvi": "trial", "tamil": "trial", "gujarati": "trial",
    },
    "expiry_kind_paid": {
        "en": "paid access", "hi": "paid access", "hinglish": "paid access",
        "punjabi": "paid access", "haryanvi": "paid access", "tamil": "paid access", "gujarati": "paid access",
    },
    "data_purged": {
        "en": ("🗑 Your <b>{count}</b> tracked item(s) have been permanently deleted "
               "after your access grace period expired without renewal.\n\n"
               "You can start fresh any time once your access is restored."),
        "hi": ("🗑 आपके <b>{count}</b> tracked item(s) हमेशा के लिए delete कर दिए गए हैं "
               "क्योंकि grace period में access renew नहीं हुआ।\n\n"
               "Access वापस मिलने पर आप कभी भी नए सिरे से शुरू कर सकते हैं।"),
        "hinglish": ("🗑 Aapke <b>{count}</b> tracked item(s) hamesha ke liye delete kar diye gaye hain "
                     "kyunki grace period me access renew nahi hua.\n\n"
                     "Access wapas milne pe aap kabhi bhi naye sire se shuru kar sakte ho."),
        "punjabi": ("🗑 Tuhade <b>{count}</b> tracked item(s) hamesha layi delete kar dite gaye ne "
                    "kyunki grace period vich access renew nahi hoya ji.\n\n"
                    "Access wapas milan te tusi kadi vi naveen sire tou shuru kar sakde ho."),
        "haryanvi": ("🗑 थारे <b>{count}</b> tracked item(s) हमेशा खात्तर मिट गए सैं "
                     "क्योंके grace period म्ह access renew कोनी होया।\n\n"
                     "access वापस मिलते ए तू फेर तै नये सिरे तै शुरू कर सकै सै।"),
        "tamil": ("🗑 உங்கள் <b>{count}</b> tracked item(s) grace period-ல் access renew "
                  "செய்யாததால் நிரந்தரமாக நீக்கப்பட்டுவிட்டன.\n\n"
                  "உங்கள் access மீட்கப்பட்டவுடன் எப்போது வேண்டுமானாலும் புதிதாக தொடங்கலாம்."),
        "gujarati": ("🗑 તમારા <b>{count}</b> tracked item(s) grace period માં access renew "
                     "ન થવાથી કાયમ માટે delete થઈ ગયા છે.\n\n"
                     "તમારો access પાછો મળે ત્યારે ગમે ત્યારે નવેસરથી શરૂ કરી શકો છો."),
    },

    # ── Free trial ───────────────────────────────────────────────────────────
    "ft_header": {
        "en": "🎁 <b>Get a free trial!</b> (Round {n} of {total})\n\n",
        "hi": "🎁 <b>Free trial पाएं!</b> (Round {n} / {total})\n\n",
        "hinglish": "🎁 <b>Free trial pao!</b> (Round {n} / {total})\n\n",
        "punjabi": "🎁 <b>Free trial jitto oye!</b> (Round {n} / {total})\n\n",
        "haryanvi": "🎁 <b>Free trial ले भाई!</b> (Round {n} / {total})\n\n",
        "tamil": "🎁 <b>Free trial பெறுங்கள்!</b> (Round {n} / {total})\n\n",
        "gujarati": "🎁 <b>Free trial મેળવો!</b> (Round {n} / {total})\n\n",
    },
    "ft_progress_first": {
        "en": ("Share Ullu Alert with a friend or group on WhatsApp, then confirm below — "
               "do this {total} times to unlock your free trial.\n\n"),
        "hi": ("Ullu Alert को WhatsApp पर किसी दोस्त या group के साथ share करें, फिर नीचे confirm करें — "
               "free trial unlock करने के लिए ये {total} बार करें।\n\n"),
        "hinglish": ("Ullu Alert ko WhatsApp pe kisi dost ya group ko share karo, phir niche confirm karo — "
                     "free trial unlock karne ke liye ye {total} baar karo.\n\n"),
        "punjabi": ("Ullu Alert nu WhatsApp te kise dost ja group naal share karo, fer heth confirm karo — "
                    "free trial unlock karan layi eh {total} vaar karo, chalo lag jao! 😄\n\n"),
        "haryanvi": ("Ullu Alert नै WhatsApp पै किसे दोस्त या ग्रुप कै गैल शेयर कर, फेर तळै confirm कर दे — "
                     "free trial अनलॉक करण खात्तर ये काम {total} बार कर, चल जुट ज्या! 😄\n\n"),
        "tamil": ("Ullu Alert-ஐ WhatsApp-ல் ஒரு நண்பருடன் அல்லது group-ல் share செய்யுங்கள், பிறகு கீழே confirm செய்யுங்கள் — "
                  "உங்கள் free trial unlock ஆக இதை {total} முறை செய்யுங்கள்.\n\n"),
        "gujarati": ("Ullu Alert ને WhatsApp પર કોઈ મિત્ર અથવા group સાથે share કરો, પછી નીચે confirm કરો — "
                     "તમારો free trial unlock કરવા આ {total} વાર કરો.\n\n"),
    },
    "ft_progress_more": {
        "en": "✅ {done}/{total} shares done — keep going!\n\n",
        "hi": "✅ {done}/{total} shares हो गए — बढ़ते रहें!\n\n",
        "hinglish": "✅ {done}/{total} shares ho gaye — lage raho!\n\n",
        "punjabi": "✅ {done}/{total} shares ho gaye — banti rehndi ee lagi raho ji! 💪\n\n",
        "haryanvi": "✅ {done}/{total} shares होगे — लाग्या रह भाई, बण जागी बात! 💪\n\n",
        "tamil": "✅ {done}/{total} shares முடிந்தது — தொடருங்கள்!\n\n",
        "gujarati": "✅ {done}/{total} shares થઈ ગયા — ચાલુ રાખો!\n\n",
    },
    "ft_waiting": {
        "en": ("⏳ Please wait {secs} seconds while you share...\n\n"
               "Tap <b>Share on WhatsApp</b> below — open the app, pick a contact or group, and send it."),
        "hi": ("⏳ Share करते समय {secs} seconds रुकें...\n\n"
               "नीचे <b>Share on WhatsApp</b> दबाएं — app खोलें, कोई contact या group चुनें, और भेज दें।"),
        "hinglish": ("⏳ Share karte waqt {secs} second ruko...\n\n"
                     "Niche <b>Share on WhatsApp</b> dabao — app kholo, koi contact ya group choose karo, aur bhej do."),
        "punjabi": ("⏳ Share karde waqt {secs} second ruko ji, thodi der sabar rakho...\n\n"
                    "Heth <b>Share on WhatsApp</b> dabao — app kholo, koi contact ja group choose karo, te bhej do."),
        "haryanvi": ("⏳ Share करते बखत {secs} सैकंड रुक ले, ज्यादा तेजी ना दिखा...\n\n"
                     "तळै <b>Share on WhatsApp</b> दबा दे — app खोल, कोए contact या group चुण, अर भेज दे।"),
        "tamil": ("⏳ Share செய்யும் போது {secs} வினாடிகள் காத்திருங்கள்...\n\n"
                  "கீழே <b>Share on WhatsApp</b>-ஐ tap செய்யுங்கள் — app-ஐ திறந்து, ஒரு contact அல்லது group-ஐ தேர்ந்தெடுத்து அனுப்புங்கள்."),
        "gujarati": ("⏳ Share કરતી વખતે {secs} સેકન્ડ રાહ જુઓ...\n\n"
                     "નીચે <b>Share on WhatsApp</b> દબાવો — app ખોલો, કોઈ contact અથવા group પસંદ કરો, અને મોકલો."),
    },
    "ft_ready": {
        "en": "✅ Shared? Tap <b>Done</b> below to continue.",
        "hi": "✅ Share कर दिया? आगे बढ़ने के लिए नीचे <b>Done</b> दबाएं।",
        "hinglish": "✅ Share kar diya? Aage badhne ke liye niche <b>Done</b> dabao.",
        "punjabi": "✅ Share kar dita? Agge vadhan layi heth <b>Done</b> dabao ji.",
        "haryanvi": "✅ शेयर करदिया के? अग्गै बढ़ण खात्तर तळै <b>Done</b> दबा दे।",
        "tamil": "✅ Share செய்துவிட்டீர்களா? தொடர கீழே <b>Done</b>-ஐ tap செய்யுங்கள்.",
        "gujarati": "✅ Share કરી દીધું? આગળ વધવા નીચે <b>Done</b> દબાવો.",
    },
    "ft_confirm": {
        "en": ("⚠️ <b>Are you sure you shared this in {total} WhatsApp groups/contacts?</b>\n\n"
               "Cheating will result in your free trial being denied and you may be "
               "permanently banned from future free trials.\n\nDo you still want to confirm?"),
        "hi": ("⚠️ <b>क्या आपने सच में इसे {total} WhatsApp groups/contacts पर share किया है?</b>\n\n"
               "धोखाधड़ी करने पर आपका free trial रद्द कर दिया जाएगा और आपको आगे के free trials से "
               "हमेशा के लिए ban किया जा सकता है।\n\nक्या आप फिर भी confirm करना चाहते हैं?"),
        "hinglish": ("⚠️ <b>Kya sach me aapne ise {total} WhatsApp groups/contacts pe share kiya hai?</b>\n\n"
                     "Cheating karne pe aapka free trial cancel ho jayega aur future free trials se "
                     "hamesha ke liye ban ho sakte ho.\n\nPhir bhi confirm karna hai?"),
        "punjabi": ("⚠️ <b>Sacchi dasso, tusi eh {total} WhatsApp groups/contacts te share kita hai?</b>\n\n"
                    "Jhooth bolna mehenga pave ga — free trial cancel ho javega te tusi hamesha layi "
                    "future free trials tou ban ho sakde ho.\n\nFer vi confirm karna hai ji?"),
        "haryanvi": ("⚠️ <b>सच बता, तन्नै ये सच म्ह {total} WhatsApp groups/contacts पै शेयर करया सै?</b>\n\n"
                     "झूठ बोल्या तो free trial कैंसिल हो ज्यागा अर आग्गै खात्तर तन्नै हमेशा खात्तर "
                     "ban भी कर सकां सैं।\n\nफेर भी confirm करणा सै भाई?"),
        "tamil": ("⚠️ <b>நீங்கள் உண்மையாக இதை {total} WhatsApp groups/contacts-ல் share செய்தீர்களா?</b>\n\n"
                  "ஏமாற்றினால் உங்கள் free trial நிராகரிக்கப்படும் மற்றும் எதிர்கால free trials-லிருந்து "
                  "நிரந்தரமாக தடை செய்யப்படலாம்.\n\nஇன்னும் confirm செய்ய வேண்டுமா?"),
        "gujarati": ("⚠️ <b>શું તમે ખરેખર આ {total} WhatsApp groups/contacts પર share કર્યું છે?</b>\n\n"
                     "ચીટિંગ કરવાથી તમારો free trial રદ થઈ જશે અને તમને ભવિષ્યના free trials માંથી "
                     "કાયમ માટે ban કરી શકાય છે.\n\nશું તમે હજુ પણ confirm કરવા માંગો છો?"),
    },
    # Inline-button labels for the /freetrial flow. Kept short — they render on
    # the buttons themselves, not in the message body.
    "ft_btn_share": {
        "en": "📤 Share on WhatsApp",
        "hi": "📤 WhatsApp पर Share करें",
        "hinglish": "📤 WhatsApp pe Share karo",
        "punjabi": "📤 WhatsApp te Share karo",
        "haryanvi": "📤 WhatsApp पै Share कर",
        "tamil": "📤 WhatsApp-ல் Share செய்யுங்கள்",
        "gujarati": "📤 WhatsApp પર Share કરો",
    },
    "ft_btn_done": {
        "en": "✅ Done",
        "hi": "✅ हो गया",
        "hinglish": "✅ Ho gaya",
        "punjabi": "✅ Ho gaya ji",
        "haryanvi": "✅ होग्या भाई",
        "tamil": "✅ முடிந்தது",
        "gujarati": "✅ થઈ ગયું",
    },
    "ft_btn_confirm": {
        "en": "✅ Yes, I confirm",
        "hi": "✅ हाँ, confirm करता हूँ",
        "hinglish": "✅ Haan, confirm",
        "punjabi": "✅ Haan ji, confirm",
        "haryanvi": "✅ हाँ भाई, पक्का",
        "tamil": "✅ ஆம், confirm செய்கிறேன்",
        "gujarati": "✅ હા, confirm કરું છું",
    },
    "ft_btn_retry": {
        "en": "🔄 Retry",
        "hi": "🔄 दोबारा",
        "hinglish": "🔄 Retry",
        "punjabi": "🔄 Fer koshish",
        "haryanvi": "🔄 फेर तै",
        "tamil": "🔄 மீண்டும்",
        "gujarati": "🔄 ફરી પ્રયત્ન",
    },
    "ft_already_used": {
        "en": ("🚫 <b>You've already used this offer.</b>\n\n"
               "The WhatsApp-share free trial can only be claimed once per account."),
        "hi": ("🚫 <b>आप ये offer पहले ही इस्तेमाल कर चुके हैं।</b>\n\n"
               "WhatsApp-share free trial हर account पर सिर्फ़ एक बार मिल सकता है।"),
        "hinglish": ("🚫 <b>Aap ye offer pehle hi use kar chuke ho.</b>\n\n"
                     "WhatsApp-share free trial har account pe sirf ek baar mil sakta hai."),
        "punjabi": ("🚫 <b>Tusi eh offer pehlan hi use kar chuke ho ji.</b>\n\n"
                    "WhatsApp-share free trial har account te sirf ikk vaar milda hai."),
        "haryanvi": ("🚫 <b>तू ये ऑफर तो पैहलेए ले चुक्या सै भाई।</b>\n\n"
                     "WhatsApp-share free trial हर account पै बस एक ए बार मिलै सै।"),
        "tamil": ("🚫 <b>நீங்கள் ஏற்கனவே இந்த offer-ஐ பயன்படுத்திவிட்டீர்கள்.</b>\n\n"
                  "WhatsApp-share free trial ஒரு account-க்கு ஒரே ஒரு முறை மட்டுமே கிடைக்கும்."),
        "gujarati": ("🚫 <b>તમે આ offer પહેલેથી જ વાપરી ચૂક્યા છો.</b>\n\n"
                     "WhatsApp-share free trial દરેક account પર ફક્ત એક જ વાર મળી શકે છે."),
    },
    "ft_request_pending": {
        "en": ("✅ <b>Thanks for sharing!</b>\n\n"
               "Your free trial request is pending admin approval. "
               "You'll be notified once approved.\n\n"
               "🦉 Join https://t.me/UlluAlert for updates!"),
        "hi": ("✅ <b>Share करने के लिए धन्यवाद!</b>\n\n"
               "आपका free trial request admin approval के लिए pending है। "
               "Approve होते ही आपको बता दिया जाएगा।\n\n"
               "🦉 Updates के लिए join करें: https://t.me/UlluAlert"),
        "hinglish": ("✅ <b>Share karne ke liye dhanyavaad!</b>\n\n"
                     "Aapka free trial request admin approval ke liye pending hai. "
                     "Approve hote hi aapko bata diya jayega.\n\n"
                     "🦉 Updates ke liye join karo: https://t.me/UlluAlert"),
        "punjabi": ("✅ <b>Share karan layi tuhada dhanwaad ji!</b>\n\n"
                    "Tuhada free trial request admin approval layi pending hai. "
                    "Approve hunde hi tuhanu dass dita javega.\n\n"
                    "🦉 Updates layi join karo: https://t.me/UlluAlert"),
        "haryanvi": ("✅ <b>शेयर करण खात्तर धन्यवाद भाई!</b>\n\n"
                     "थारा free trial request admin approval खात्तर pending सै। "
                     "Approve होते ए तन्नै बता दयुँगा।\n\n"
                     "🦉 Updates खात्तर join कर: https://t.me/UlluAlert"),
        "tamil": ("✅ <b>Share செய்ததற்கு நன்றி!</b>\n\n"
                  "உங்கள் free trial request admin approval-க்காக pending-ல் உள்ளது. "
                  "approve ஆனவுடன் உங்களுக்கு தெரிவிக்கப்படும்.\n\n"
                  "🦉 Updates-க்கு https://t.me/UlluAlert-ஐ join செய்யுங்கள்!"),
        "gujarati": ("✅ <b>Share કરવા બદલ આભાર!</b>\n\n"
                     "તમારી free trial request admin approval માટે pending છે. "
                     "Approve થતાં જ તમને જણાવવામાં આવશે.\n\n"
                     "🦉 Updates માટે https://t.me/UlluAlert join કરો!"),
    },
    "ft_wa_share_text": {
        "en": ("🚨 PS5 restock? New iPhone drop? Don't miss it again!\n\n"
               "I use Ullu Alert (100% FREE) — it watches products 24/7 and pings me "
               "the SECOND they're back in stock, so I never miss a restock. 🔥\n\n"
               "Try it free: {link}"),
        "hi": ("🚨 PS5 restock? नया iPhone drop? अब मत चूको!\n\n"
               "मैं Ullu Alert use करता हूँ (100% FREE) — ये products को 24/7 watch करता है और stock "
               "आते ही तुरंत ping कर देता है, तो कोई restock miss नहीं होता। 🔥\n\n"
               "Free try करो: {link}"),
        "hinglish": ("🚨 PS5 restock? Naya iPhone drop? Ab mat chuko!\n\n"
                     "Main Ullu Alert use karta hoon (100% FREE) — ye products ko 24/7 watch karta hai aur "
                     "stock aate hi turant ping kar deta hai, to koi restock miss nahi hota. 🔥\n\n"
                     "Free try karo: {link}"),
        "punjabi": ("🚨 PS5 restock? Naveen iPhone aa gaya? Hun mauka na gawao!\n\n"
                    "Main Ullu Alert use karda haan (100% FREE) — eh products nu 24/7 nazar rakhda hai te "
                    "stock aande hi turant ping kar denda hai, tan koi restock miss nahi hunda. 🔥\n\n"
                    "Free try karo: {link}"),
        "haryanvi": ("🚨 PS5 फेर आग्या? नया iPhone आग्या? इस बार मौका मत चूकिये!\n\n"
                     "मैं Ullu Alert यूज़ करूं सूं (100% FREE) — ये products नै 24/7 नजर राखै सै अर "
                     "स्टॉक आते ए फट ping कर देवै सै, तो कदे भी restock मिस कोनी होन्दा। 🔥\n\n"
                     "Free म्ह try कर: {link}"),
        "tamil": ("🚨 PS5 restock? புது iPhone வந்ததா? இனி மிஸ் பண்ணாதீங்க!\n\n"
                  "நான் Ullu Alert பயன்படுத்துகிறேன் (100% FREE) — இது products-ஐ 24/7 கண்காணித்து "
                  "stock வந்த உடனே எனக்கு ping அனுப்பும், அதனால் நான் ஒரு restock-ஐயும் தவறவிடுவதில்லை. 🔥\n\n"
                  "இலவசமாக முயற்சிக்கவும்: {link}"),
        "gujarati": ("🚨 PS5 restock? નવો iPhone આવ્યો? હવે ચૂકતા નહીં!\n\n"
                     "હું Ullu Alert વાપરું છું (100% FREE) — તે products ને 24/7 જુએ છે અને stock "
                     "આવતાં જ મને તરત ping કરે છે, એટલે હું ક્યારેય restock miss નથી કરતો. 🔥\n\n"
                     "મફતમાં try કરો: {link}"),
    },
    "ft_admin_no_need": {
        "en": "The admin account doesn't need a trial.",
        "hi": "Admin account को trial की ज़रूरत नहीं है।",
        "hinglish": "Admin account ko trial ki zaroorat nahi hai.",
        "punjabi": "Admin account nu trial di lod nahi hai.",
        "haryanvi": "Admin account नै trial की जरूरत कोनी।",
        "tamil": "Admin account-க்கு trial தேவையில்லை.",
        "gujarati": "Admin account ને trial ની જરૂર નથી.",
    },

    # ── Add flow ─────────────────────────────────────────────────────────────
    "add_instructions": {
        "en": ("📦 <b>Add product(s)</b>\n\n"
               "<b>Option A — Bulk (one per line):</b>\n"
               "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
               "<b>Option B — Single:</b> just send the product name, then the URL in the next step.\n\n"
               "Type /cancel to abort."),
        "hi": ("📦 <b>Product(s) जोड़ें</b>\n\n"
               "<b>Option A — Bulk (एक लाइन में एक):</b>\n"
               "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
               "<b>Option B — Single:</b> बस product का नाम भेजें, फिर अगले step में URL।\n\n"
               "रद्द करने के लिए /cancel लिखें।"),
        "hinglish": ("📦 <b>Product(s) add karo</b>\n\n"
                     "<b>Option A — Bulk (ek line me ek):</b>\n"
                     "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
                     "<b>Option B — Single:</b> bas product ka naam bhejo, phir agle step me URL.\n\n"
                     "Cancel karne ke liye /cancel likho."),
        "punjabi": ("📦 <b>Product(s) add karo ji</b>\n\n"
                    "<b>Option A — Bulk (ikk line 'ch ikk):</b>\n"
                    "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
                    "<b>Option B — Single:</b> bas product da naam bhejo, fer agle step vich URL.\n\n"
                    "Cancel karan layi /cancel likho."),
        "haryanvi": ("📦 <b>Product(s) जोड़</b>\n\n"
                     "<b>Option A — Bulk (एक लाइन म्ह एक):</b>\n"
                     "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
                     "<b>Option B — Single:</b> बस product का नाम भेज दे, फेर अगले step म्ह URL।\n\n"
                     "रद्द करण खात्तर /cancel लिख दे।"),
        "tamil": ("📦 <b>Product(s) சேர்க்கவும்</b>\n\n"
                  "<b>Option A — Bulk (ஒரு வரிக்கு ஒன்று):</b>\n"
                  "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
                  "<b>Option B — Single:</b> product பெயரை மட்டும் அனுப்புங்கள், பிறகு அடுத்த step-ல் URL.\n\n"
                  "நிறுத்த /cancel-ஐ type செய்யவும்."),
        "gujarati": ("📦 <b>Product(s) ઉમેરો</b>\n\n"
                     "<b>Option A — Bulk (એક લાઇનમાં એક):</b>\n"
                     "<code>Watch | https://amazon.in/…\nShirt | https://flipkart.com/…</code>\n\n"
                     "<b>Option B — Single:</b> ફક્ત product નું નામ મોકલો, પછી આગલા step માં URL.\n\n"
                     "રદ કરવા /cancel લખો."),
    },
    "add_name_saved": {
        "en": ("✅ Name saved: <b>{name}</b>\n\n"
               "Step 2 of 2 — Send me the <b>product URL</b>.\n"
               "Paste <b>multiple URLs (one per line)</b> to add several products at once.\n"
               "Supported: {sites}"),
        "hi": ("✅ नाम save हुआ: <b>{name}</b>\n\n"
               "Step 2 of 2 — मुझे <b>product URL</b> भेजें।\n"
               "एक साथ कई products जोड़ने के लिए <b>कई URLs (एक लाइन में एक)</b> paste करें।\n"
               "Supported: {sites}"),
        "hinglish": ("✅ Naam save hua: <b>{name}</b>\n\n"
                     "Step 2 of 2 — Mujhe <b>product URL</b> bhejo.\n"
                     "Ek saath kai products add karne ke liye <b>kai URLs (ek line me ek)</b> paste karo.\n"
                     "Supported: {sites}"),
        "punjabi": ("✅ Naam save ho gaya: <b>{name}</b>\n\n"
                    "Step 2 of 2 — Mainu <b>product URL</b> bhejo ji.\n"
                    "Ikko vaari kayi products add karan layi <b>kayi URLs (ikk line 'ch ikk)</b> paste karo.\n"
                    "Supported: {sites}"),
        "haryanvi": ("✅ नाम सेव होग्या: <b>{name}</b>\n\n"
                     "Step 2 of 2 — मन्नै <b>product URL</b> भेज दे।\n"
                     "एक साथ भोत सारे products जोड़ण खात्तर <b>भोत सारे URLs (एक लाइन म्ह एक)</b> paste कर दे।\n"
                     "Supported: {sites}"),
        "tamil": ("✅ பெயர் save ஆனது: <b>{name}</b>\n\n"
                  "Step 2 of 2 — எனக்கு <b>product URL</b>-ஐ அனுப்புங்கள்.\n"
                  "ஒரே நேரத்தில் பல products சேர்க்க <b>பல URLs (ஒரு வரிக்கு ஒன்று)</b> paste செய்யுங்கள்.\n"
                  "Supported: {sites}"),
        "gujarati": ("✅ નામ save થયું: <b>{name}</b>\n\n"
                     "Step 2 of 2 — મને <b>product URL</b> મોકલો.\n"
                     "એકસાથે ઘણા products ઉમેરવા <b>ઘણા URLs (એક લાઇનમાં એક)</b> paste કરો.\n"
                     "Supported: {sites}"),
    },
    "add_empty_input": {
        "en": "Input cannot be empty. Please try again.",
        "hi": "Input खाली नहीं हो सकता। कृपया दोबारा try करें।",
        "hinglish": "Input khaali nahi ho sakta. Dobara try karo.",
        "punjabi": "Input khaali nahi ho sakda ji. Dobara try karo.",
        "haryanvi": "Input खाली कोनी हो सकता। फेर तै try कर।",
        "tamil": "Input காலியாக இருக்கக்கூடாது. மீண்டும் முயற்சிக்கவும்.",
        "gujarati": "Input ખાલી ન હોઈ શકે. કૃપા કરી ફરી try કરો.",
    },
    "add_invalid_url": {
        "en": "⚠️ That doesn't look like a valid URL. Please paste the full link (starting with https://).",
        "hi": "⚠️ ये सही URL नहीं लग रहा। कृपया पूरा link paste करें (https:// से शुरू)।",
        "hinglish": "⚠️ Ye sahi URL nahi lag raha. Poora link paste karo (https:// se shuru hone wala).",
        "punjabi": "⚠️ Eh sahi URL nahi lagda ji. Poora link paste karo (https:// naal shuru hona chahida).",
        "haryanvi": "⚠️ ये सही URL कोनी लाग रहा। पूरा link paste कर दे (https:// तै शुरू होणा चाहिए)।",
        "tamil": "⚠️ இது சரியான URL போல் தெரியவில்லை. முழு link-ஐ paste செய்யுங்கள் (https:// உடன் தொடங்க வேண்டும்).",
        "gujarati": "⚠️ આ યોગ્ય URL લાગતું નથી. કૃપા કરી પૂરી link paste કરો (https:// થી શરૂ થવી જોઈએ).",
    },
    "add_unsupported": {
        "en": ("❌ <b>Unsupported website.</b>\n\nSupported: {sites}\n\n"
               "Please send a link from one of these sites."),
        "hi": ("❌ <b>ये website supported नहीं है।</b>\n\nSupported: {sites}\n\n"
               "कृपया इनमें से किसी site का link भेजें।"),
        "hinglish": ("❌ <b>Ye website supported nahi hai.</b>\n\nSupported: {sites}\n\n"
                     "Inme se kisi site ka link bhejo."),
        "punjabi": ("❌ <b>Eh website supported nahi hai ji.</b>\n\nSupported: {sites}\n\n"
                    "Inhan 'cho kise site da link bhejo."),
        "haryanvi": ("❌ <b>ये website supported कोनी।</b>\n\nSupported: {sites}\n\n"
                     "इन म्हतै कोए site का link भेज दे।"),
        "tamil": ("❌ <b>இந்த website ஆதரிக்கப்படவில்லை.</b>\n\nSupported: {sites}\n\n"
                  "இவற்றில் ஒரு site-ன் link-ஐ அனுப்புங்கள்."),
        "gujarati": ("❌ <b>આ website supported નથી.</b>\n\nSupported: {sites}\n\n"
                     "કૃપા કરી આમાંથી કોઈ site ની link મોકલો."),
    },
    "amazon_target_prompt": {
        "en": ("💰 <b>Set a target price (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
               "Send a target price (e.g. <code>1299</code> or <code>1299.99</code>) to only get "
               "alerted when the price drops to or below that amount.\n\n"
               "Or send /skip to get alerted at any price."),
        "hi": ("💰 <b>Target price set करें (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
               "Target price भेजें (जैसे <code>1299</code> या <code>1299.99</code>) ताकि alert तभी मिले "
               "जब price उतनी या उससे कम हो जाए।\n\n"
               "या किसी भी price पर alert पाने के लिए /skip भेजें।"),
        "hinglish": ("💰 <b>Target price set karo (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
                     "Target price bhejo (jaise <code>1299</code> ya <code>1299.99</code>) taki alert tabhi mile "
                     "jab price utni ya usse kam ho jaye.\n\n"
                     "Ya kisi bhi price pe alert paane ke liye /skip bhejo."),
        "punjabi": ("💰 <b>Target price set karo (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
                    "Target price bhejo (jiven <code>1299</code> ja <code>1299.99</code>) tan ke alert tadon mile "
                    "jado price ohna jinni ja usto ghatt ho jave.\n\n"
                    "Ja kise vi price te alert layi /skip bhejo."),
        "haryanvi": ("💰 <b>Target price सेट कर (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
                     "Target price भेज दे (जिसा <code>1299</code> या <code>1299.99</code>) ताके alert तबी मिलै "
                     "जब price उतणी या उस्तै कम हो ज्या।\n\n"
                     "या फेर किसे भी price पै alert पाण खात्तर /skip भेज दे।"),
        "tamil": ("💰 <b>Target price அமைக்கவும் (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
                  "Price அந்த அளவுக்கு அல்லது அதற்குக் கீழ் வரும்போது மட்டும் alert பெற target price அனுப்புங்கள் "
                  "(எ.கா. <code>1299</code> அல்லது <code>1299.99</code>).\n\n"
                  "அல்லது எந்த price-லும் alert பெற /skip அனுப்புங்கள்."),
        "gujarati": ("💰 <b>Target price set કરો (optional)</b>\n\nTracking: <b>{name}</b>\n\n"
                     "Price એટલી અથવા તેનાથી ઓછી થાય ત્યારે જ alert મેળવવા target price મોકલો "
                     "(દા.ત. <code>1299</code> અથવા <code>1299.99</code>).\n\n"
                     "અથવા કોઈપણ price પર alert મેળવવા /skip મોકલો."),
    },
    "target_invalid": {
        "en": ("⚠️ That doesn't look like a valid price. Send a number like <code>1299</code> or "
               "<code>1299.99</code>, or /skip to track at any price."),
        "hi": ("⚠️ ये सही price नहीं लग रही। <code>1299</code> या <code>1299.99</code> जैसा number भेजें, "
               "या किसी भी price पर track करने के लिए /skip भेजें।"),
        "hinglish": ("⚠️ Ye sahi price nahi lag rahi. <code>1299</code> ya <code>1299.99</code> jaisa number bhejo, "
                     "ya kisi bhi price pe track karne ke liye /skip bhejo."),
        "punjabi": ("⚠️ Eh sahi price nahi lagdi ji. <code>1299</code> ja <code>1299.99</code> jehda number bhejo, "
                    "ja kise vi price te track karan layi /skip bhejo."),
        "haryanvi": ("⚠️ ये सही price कोनी लाग रही। <code>1299</code> या <code>1299.99</code> जिसा नंबर भेज दे, "
                     "या फेर किसे भी price पै track करण खात्तर /skip भेज दे।"),
        "tamil": ("⚠️ இது சரியான price போல் தெரியவில்லை. <code>1299</code> அல்லது <code>1299.99</code> போன்ற எண்ணை அனுப்புங்கள், "
                  "அல்லது எந்த price-லும் track செய்ய /skip அனுப்புங்கள்."),
        "gujarati": ("⚠️ આ યોગ્ય price લાગતી નથી. <code>1299</code> અથવા <code>1299.99</code> જેવો number મોકલો, "
                     "અથવા કોઈપણ price પર track કરવા /skip મોકલો."),
    },
    "product_added": {
        "en": ("🎉 <b>Product added!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
               "🔗 <b>URL:</b> {url}\n\nI'll notify you as soon as it's back in stock!"),
        "hi": ("🎉 <b>Product जोड़ा गया!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
               "🔗 <b>URL:</b> {url}\n\nStock में वापस आते ही मैं आपको बता दूँगा!"),
        "hinglish": ("🎉 <b>Product add ho gaya!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
                     "🔗 <b>URL:</b> {url}\n\nStock me wapas aate hi main aapko bata dunga!"),
        "punjabi": ("🎉 <b>Product add ho gaya ji!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
                    "🔗 <b>URL:</b> {url}\n\nStock vich wapas aunde hi main tuhanu dass devanga, promise! 🦉"),
        "haryanvi": ("🎉 <b>Product जुड़ग्या भाई!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
                     "🔗 <b>URL:</b> {url}\n\nस्टॉक म्ह वापस आते ए मैं तन्नै फट बता दयुँगा, पक्का! 🦉"),
        "tamil": ("🎉 <b>Product சேர்க்கப்பட்டது!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
                  "🔗 <b>URL:</b> {url}\n\nStock-க்கு திரும்பி வந்தவுடன் உங்களுக்கு தெரிவிப்பேன்!"),
        "gujarati": ("🎉 <b>Product ઉમેરાયું!</b>\n\n📌 <b>Name:</b> {name}\n🛒 <b>Site:</b> {site}\n"
                     "🔗 <b>URL:</b> {url}\n\nStock માં પાછું આવે કે તરત જ હું તમને જણાવીશ!"),
    },
    "cancelled": {
        "en": "❌ Cancelled.",
        "hi": "❌ रद्द कर दिया।",
        "hinglish": "❌ Cancel kar diya.",
        "punjabi": "❌ Cancel kar dita ji.",
        "haryanvi": "❌ Cancel करदिया।",
        "tamil": "❌ Cancel செய்யப்பட்டது.",
        "gujarati": "❌ Cancel કરી દીધું.",
    },

    # ── List / remove / check / search / stores empties+headers ──────────────
    "list_empty": {
        "en": "📭 You have no tracked products yet.\nUse /add to start tracking one!",
        "hi": "📭 अभी आपके कोई tracked products नहीं हैं।\nTrack करना शुरू करने के लिए /add इस्तेमाल करें!",
        "hinglish": "📭 Abhi aapke koi tracked products nahi hain.\nTrack karna shuru karne ke liye /add use karo!",
        "punjabi": "📭 Abhi tuhade kol koi tracked products nahi ne ji.\nShuru karan layi /add use karo!",
        "haryanvi": "📭 अबार थारे कोए tracked products कोनी।\nशुरू करण खात्तर /add दबा दे!",
        "tamil": "📭 தற்போது உங்களிடம் tracked products இல்லை.\nதொடங்க /add-ஐ பயன்படுத்துங்கள்!",
        "gujarati": "📭 અત્યારે તમારી પાસે કોઈ tracked products નથી.\nશરૂ કરવા /add નો ઉપયોગ કરો!",
    },
    "list_header": {
        "en": "📋 <b>Your Tracked Products</b>\n",
        "hi": "📋 <b>आपके Tracked Products</b>\n",
        "hinglish": "📋 <b>Aapke Tracked Products</b>\n",
        "punjabi": "📋 <b>Tuhade Tracked Products</b>\n",
        "haryanvi": "📋 <b>थारे Tracked Products</b>\n",
        "tamil": "📋 <b>உங்கள் Tracked Products</b>\n",
        "gujarati": "📋 <b>તમારા Tracked Products</b>\n",
    },
    "remove_empty": {
        "en": "📭 You have no products to remove.\nUse /add to start tracking one!",
        "hi": "📭 हटाने के लिए आपके कोई products नहीं हैं।\nTrack करने के लिए /add इस्तेमाल करें!",
        "hinglish": "📭 Hataane ke liye aapke koi products nahi hain.\nTrack karne ke liye /add use karo!",
        "punjabi": "📭 Hatan layi tuhade kol koi products nahi ne.\nTrack karan layi /add use karo!",
        "haryanvi": "📭 हटाण खात्तर थारे कोए products कोनी।\nट्रैक करण खात्तर /add दबा दे!",
        "tamil": "📭 நீக்க உங்களிடம் products இல்லை.\ntrack செய்ய /add-ஐ பயன்படுத்துங்கள்!",
        "gujarati": "📭 હટાવવા માટે તમારી પાસે products નથી.\nTrack કરવા /add નો ઉપયોગ કરો!",
    },
    "remove_prompt": {
        "en": "🗑 <b>Select a product to remove:</b>",
        "hi": "🗑 <b>हटाने के लिए कोई product चुनें:</b>",
        "hinglish": "🗑 <b>Hataane ke liye koi product choose karo:</b>",
        "punjabi": "🗑 <b>Hatan layi koi product choose karo ji:</b>",
        "haryanvi": "🗑 <b>हटाण खात्तर कोए product छाँट ले:</b>",
        "tamil": "🗑 <b>நீக்க ஒரு product-ஐ தேர்ந்தெடுங்கள்:</b>",
        "gujarati": "🗑 <b>હટાવવા માટે કોઈ product પસંદ કરો:</b>",
    },
    "check_empty": {
        "en": "📭 You have no tracked products yet.\nUse /add to start tracking one!",
        "hi": "📭 अभी आपके कोई tracked products नहीं हैं।\nTrack करने के लिए /add इस्तेमाल करें!",
        "hinglish": "📭 Abhi aapke koi tracked products nahi hain.\nTrack karne ke liye /add use karo!",
        "punjabi": "📭 Abhi tuhade kol koi tracked products nahi ne.\nTrack karan layi /add use karo!",
        "haryanvi": "📭 अबार थारे कोए tracked products कोनी।\nट्रैक करण खात्तर /add दबा दे!",
        "tamil": "📭 தற்போது உங்களிடம் tracked products இல்லை.\ntrack செய்ய /add-ஐ பயன்படுத்துங்கள்!",
        "gujarati": "📭 અત્યારે તમારી પાસે કોઈ tracked products નથી.\nTrack કરવા /add નો ઉપયોગ કરો!",
    },
    "check_filter_prompt": {
        "en": "🏪 <b>Filter by store</b>\n\nPick a store to check, or check all at once:",
        "hi": "🏪 <b>Store से filter करें</b>\n\nCheck करने के लिए कोई store चुनें, या सब एक साथ check करें:",
        "hinglish": "🏪 <b>Store se filter karo</b>\n\nCheck karne ke liye koi store choose karo, ya sab ek saath check karo:",
        "punjabi": "🏪 <b>Store naal filter karo</b>\n\nCheck karan layi koi store choose karo, ja sare iko vaari check karo:",
        "haryanvi": "🏪 <b>Store तै filter कर</b>\n\nCheck करण खात्तर कोए store छाँट, या सारे एक साथ check कर:",
        "tamil": "🏪 <b>Store மூலம் filter செய்யவும்</b>\n\nCheck செய்ய ஒரு store-ஐ தேர்ந்தெடுக்கவும், அல்லது எல்லாவற்றையும் ஒரே நேரத்தில் check செய்யவும்:",
        "gujarati": "🏪 <b>Store દ્વારા filter કરો</b>\n\nCheck કરવા કોઈ store પસંદ કરો, અથવા બધા એકસાથે check કરો:",
    },
    "search_empty": {
        "en": "📭 You have no tracked products to search.\nUse /add to start tracking one!",
        "hi": "📭 खोजने के लिए आपके कोई tracked products नहीं हैं।\nTrack करने के लिए /add इस्तेमाल करें!",
        "hinglish": "📭 Search karne ke liye aapke koi tracked products nahi hain.\nTrack karne ke liye /add use karo!",
        "punjabi": "📭 Search karan layi tuhade kol koi tracked products nahi ne.\nTrack karan layi /add use karo!",
        "haryanvi": "📭 ढूंढण खात्तर थारे कोए tracked products कोनी।\nट्रैक करण खात्तर /add दबा दे!",
        "tamil": "📭 தேட உங்களிடம் tracked products இல்லை.\ntrack செய்ய /add-ஐ பயன்படுத்துங்கள்!",
        "gujarati": "📭 શોધવા માટે તમારી પાસે tracked products નથી.\nTrack કરવા /add નો ઉપયોગ કરો!",
    },
    "stores_intro": {
        "en": "🏪 <b>Supported Stores</b>\n\nWe currently support tracking on these stores:\n",
        "hi": "🏪 <b>Supported Stores</b>\n\nअभी हम इन stores पर tracking support करते हैं:\n",
        "hinglish": "🏪 <b>Supported Stores</b>\n\nAbhi hum in stores pe tracking support karte hain:\n",
        "punjabi": "🏪 <b>Supported Stores</b>\n\nAbhi asi enna stores te tracking support karde haan:\n",
        "haryanvi": "🏪 <b>Supported Stores</b>\n\nअबार हम इन stores पै tracking support करां सां:\n",
        "tamil": "🏪 <b>ஆதரிக்கப்படும் Stores</b>\n\nதற்போது இந்த stores-ல் tracking support செய்கிறோம்:\n",
        "gujarati": "🏪 <b>Supported Stores</b>\n\nહાલમાં અમે આ stores પર tracking support કરીએ છીએ:\n",
    },
    "coming_soon_croma": {
        "en": ("🚧 <b>Croma tracking is temporarily unavailable.</b>\n\n"
               "We found reliability issues with Croma stock detection and pulled it "
               "while we fix them, rather than risk sending you wrong alerts. "
               "Check back soon, or track this product on another supported store in the meantime."),
        "hi": ("🚧 <b>Croma tracking अभी temporarily unavailable है।</b>\n\n"
               "Croma के stock detection में कुछ reliability issues मिले, इसलिए गलत alerts भेजने के risk "
               "से बचने के लिए हमने इसे फिलहाल हटा दिया है। जल्द वापस check करें, या तब तक किसी और "
               "supported store पर ये product track करें।"),
        "hinglish": ("🚧 <b>Croma tracking abhi temporarily unavailable hai.</b>\n\n"
                     "Croma ke stock detection me kuch reliability issues mile, isliye galat alerts bhejne ke "
                     "risk se bachne ke liye humne ise filhaal hata diya hai. Jald wapas check karo, ya tab tak "
                     "kisi aur supported store pe ye product track karo."),
        "punjabi": ("🚧 <b>Croma tracking abhi temporarily unavailable hai ji.</b>\n\n"
                    "Croma de stock detection vich kuch reliability issues labhe, es layi galat alerts bhejan de "
                    "risk to bachan layi asi ise filhaal hata dita hai. Jaldi wapas check karo, ja odon tak "
                    "kise hor supported store te eh product track karo."),
        "haryanvi": ("🚧 <b>Croma tracking अबार खात्तर बंद सै।</b>\n\n"
                     "Croma के stock detection म्ह कुछ गड़बड़ मिली, इस खात्तर गलत alerts भेजण के risk "
                     "तै बचण खात्तर म्हने इसनै फिलहाल हटा दिया सै। जल्दी वापस चैक कर, या फेर तब ताहीं कोए और "
                     "supported store पै ये product ट्रैक कर ले।"),
        "tamil": ("🚧 <b>Croma tracking தற்காலிகமாக கிடைக்கவில்லை.</b>\n\n"
                  "Croma-வின் stock detection-ல் சில reliability issues கண்டோம், தவறான alerts அனுப்பும் "
                  "risk-ஐ தவிர்க்க தற்காலிகமாக நீக்கியுள்ளோம். விரைவில் மீண்டும் check செய்யுங்கள், அல்லது இதற்கிடையில் வேறு "
                  "ஆதரிக்கப்படும் store-ல் இந்த product-ஐ track செய்யுங்கள்."),
        "gujarati": ("🚧 <b>Croma tracking હાલ પૂરતું ઉપલબ્ધ નથી.</b>\n\n"
                     "Croma ના stock detection માં અમને reliability issues મળ્યા, એટલે ખોટા alerts મોકલવાના "
                     "risk થી બચવા અમે તેને હાલ પૂરતું હટાવી દીધું છે. જલ્દી પાછા check કરો, અથવા ત્યાં સુધી બીજા "
                     "supported store પર આ product track કરો."),
    },

    # ── /language ────────────────────────────────────────────────────────────
    "language_prompt": {
        "en": "🌐 <b>Choose your language</b>\nAffects the main messages you see — commands stay the same.",
        "hi": "🌐 <b>अपनी भाषा चुनें</b>\nइससे आपके main messages बदलेंगे — commands वही रहेंगे।",
        "hinglish": "🌐 <b>Apni language choose karo</b>\nIsse aapke main messages badlenge — commands wahi rahenge.",
        "punjabi": "🌐 <b>Apni boli choose karo ji</b>\nEsse tuhade main messages badalange — commands ohi rahinge.",
        "haryanvi": "🌐 <b>अपणी भाषा चुण</b>\nइस्तै थारे main messages बदलैंगे — commands वोही रहवैंगे।",
        "tamil": "🌐 <b>உங்கள் மொழியை தேர்ந்தெடுங்கள்</b>\nஇது நீங்கள் காணும் main messages-ஐ மாற்றும் — commands அப்படியே இருக்கும்.",
        "gujarati": "🌐 <b>તમારી ભાષા પસંદ કરો</b>\nઆ તમારા main messages બદલશે — commands એ જ રહેશે.",
    },
    "language_set": {
        "en": "✅ Language set to <b>English</b>.",
        "hi": "✅ भाषा <b>हिंदी</b> set कर दी गई है।",
        "hinglish": "✅ Language <b>Hinglish</b> set kar di gayi hai.",
        "punjabi": "✅ Boli <b>ਪੰਜਾਬੀ</b> set kar ditti gayi hai, waah ji waah! 🎉",
        "haryanvi": "✅ भाषा <b>हरियाणवी</b> सेट होगी भाई, मजा आग्या! 🎉",
        "tamil": "✅ மொழி <b>தமிழ்</b> ஆக அமைக்கப்பட்டது.",
        "gujarati": "✅ ભાષા <b>ગુજરાતી</b> set કરવામાં આવી છે.",
    },
    "language_welcome_prompt": {
        "en": "👋 Welcome! First, choose your language:",
        "hi": "👋 स्वागत है! पहले, अपनी भाषा चुनें:",
        "hinglish": "👋 Welcome! Pehle, apni language choose karo:",
        "punjabi": "👋 Welcome ji! Pehlan, apni boli choose karo:",
        "haryanvi": "👋 आजा भाई, स्वागत सै! पैहले, अपणी भाषा चुण:",
        "tamil": "👋 வரவேற்கிறோம்! முதலில், உங்கள் மொழியை தேர்ந்தெடுங்கள்:",
        "gujarati": "👋 સ્વાગત છે! પહેલા, તમારી ભાષા પસંદ કરો:",
    },

    # ── WhatsApp channel forwarding ─────────────────────────────────────────
    "whatsapp_usage": {
        "en": ("📲 <b>Link your WhatsApp Channel/Community</b>\n\n"
               "Usage: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
               "Add the admin as a member/admin of your Channel or Community first, "
               "then send its invite link here. The admin will review and approve it "
               "before your \"back in stock\" alerts start forwarding there too."),
        "hi": ("📲 <b>अपना WhatsApp Channel/Community जोड़ें</b>\n\n"
               "इस्तेमाल करें: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
               "पहले admin को अपने Channel या Community में member/admin बनाएं, "
               "फिर उसका invite link यहाँ भेजें। Admin इसे review करके approve करेंगे, "
               "उसके बाद आपके \"back in stock\" alerts वहाँ भी forward होने लगेंगे।"),
        "hinglish": ("📲 <b>Apna WhatsApp Channel/Community link karo</b>\n\n"
                     "Usage: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
                     "Pehle admin ko apne Channel ya Community me member/admin banao, "
                     "phir uska invite link yahan bhejo. Admin ise review karke approve "
                     "karega, uske baad aapke \"back in stock\" alerts wahan bhi "
                     "forward hone lagenge."),
        "punjabi": ("📲 <b>Apna WhatsApp Channel/Community link karo ji</b>\n\n"
                    "Use karo: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
                    "Pehlan admin nu apne Channel ja Community 'ch member/admin banao, "
                    "fer ohda invite link ithe bhejo. Admin review karke approve karega, "
                    "usde baad tuhade \"back in stock\" alerts othe vi jaan lagn ge!"),
        "haryanvi": ("📲 <b>अपणा WhatsApp Channel/Community जोड़ दे</b>\n\n"
                     "इसा यूज़ कर: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
                     "पैहले admin नै अपणे Channel या Community म्ह member/admin बणा, "
                     "फेर उसका invite link न्ह्यां भेज दे। Admin चैक करके approve "
                     "करैगा, बाद म्ह थारे \"back in stock\" alerts ओड़ैभी जाणा शुरू "
                     "होज्यांगे।"),
        "tamil": ("📲 <b>உங்கள் WhatsApp Channel/Community-ஐ இணைக்கவும்</b>\n\n"
                  "பயன்பாடு: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
                  "முதலில் admin-ஐ உங்கள் Channel அல்லது Community-ல் member/admin "
                  "ஆக சேர்க்கவும், பிறகு அதன் invite link-ஐ இங்கே அனுப்பவும். Admin "
                  "பரிசீலித்து approve செய்த பிறகு உங்கள் \"back in stock\" alerts "
                  "அங்கும் forward ஆகும்."),
        "gujarati": ("📲 <b>તમારું WhatsApp Channel/Community લિંક કરો</b>\n\n"
                     "ઉપયોગ: <code>/setwhatsapp &lt;invite link&gt;</code>\n\n"
                     "પહેલા admin ને તમારા Channel અથવા Community માં member/admin "
                     "બનાવો, પછી તેની invite link અહીં મોકલો. Admin તેની સમીક્ષા કરીને "
                     "approve કરશે, ત્યાર પછી તમારા \"back in stock\" alerts ત્યાં પણ "
                     "forward થવા લાગશે."),
    },
    "whatsapp_link_invalid": {
        "en": ("⚠️ That doesn't look like a WhatsApp Channel/Community invite link.\n\n"
               "It should look like <code>https://whatsapp.com/channel/...</code> or "
               "<code>https://chat.whatsapp.com/...</code>."),
        "hi": ("⚠️ ये WhatsApp Channel/Community का invite link नहीं लग रहा।\n\n"
               "ये कुछ ऐसा दिखना चाहिए: <code>https://whatsapp.com/channel/...</code> "
               "या <code>https://chat.whatsapp.com/...</code>."),
        "hinglish": ("⚠️ Ye WhatsApp Channel/Community ka invite link nahi lag raha.\n\n"
                     "Aisa dikhna chahiye: <code>https://whatsapp.com/channel/...</code> "
                     "ya <code>https://chat.whatsapp.com/...</code>."),
        "punjabi": ("⚠️ Eh WhatsApp Channel/Community da invite link nahi lagda ji.\n\n"
                    "Ajeha dikhna chahida: <code>https://whatsapp.com/channel/...</code> "
                    "ja <code>https://chat.whatsapp.com/...</code>."),
        "haryanvi": ("⚠️ ये WhatsApp Channel/Community का invite link कोनी लाग रहा।\n\n"
                     "इसा दिखणा चाहिए: <code>https://whatsapp.com/channel/...</code> "
                     "या <code>https://chat.whatsapp.com/...</code>."),
        "tamil": ("⚠️ இது WhatsApp Channel/Community invite link போல் தெரியவில்லை.\n\n"
                  "இது இப்படி இருக்க வேண்டும்: <code>https://whatsapp.com/channel/...</code> "
                  "அல்லது <code>https://chat.whatsapp.com/...</code>."),
        "gujarati": ("⚠️ આ WhatsApp Channel/Community ની invite link લાગતી નથી.\n\n"
                     "આના જેવી હોવી જોઈએ: <code>https://whatsapp.com/channel/...</code> "
                     "અથવા <code>https://chat.whatsapp.com/...</code>."),
    },
    "whatsapp_registered_pending": {
        "en": ("✅ <b>Channel link saved!</b>\n\n"
               "It's now pending admin approval — the admin needs to join your "
               "Channel/Community before forwarding can start. Check /whatsappstatus "
               "any time."),
        "hi": ("✅ <b>Channel link save हो गया!</b>\n\n"
               "अब ये admin approval का इंतज़ार कर रहा है — forwarding शुरू होने से "
               "पहले admin को आपके Channel/Community में join करना होगा। कभी भी "
               "/whatsappstatus से status देख सकते हैं।"),
        "hinglish": ("✅ <b>Channel link save ho gaya!</b>\n\n"
                     "Ab ye admin approval ka wait kar raha hai — forwarding shuru "
                     "hone se pehle admin ko aapke Channel/Community me join karna "
                     "hoga. Kabhi bhi /whatsappstatus se status dekh sakte ho."),
        "punjabi": ("✅ <b>Channel link save ho gaya ji!</b>\n\n"
                    "Hun eh admin approval da wait kar riha hai — forwarding shuru "
                    "hon tou pehlan admin nu tuhade Channel/Community 'ch join karna "
                    "pavega. Kadi vi /whatsappstatus naal status vekh sakde ho."),
        "haryanvi": ("✅ <b>Channel link सेव होगी!</b>\n\n"
                     "अब ये admin approval का इंतजार कर रही सै — forwarding चालू "
                     "होण तै पैहले admin नै थारे Channel/Community म्ह join करणा "
                     "पड़ैगा। कदे भी /whatsappstatus तै स्टेटस देख ले।"),
        "tamil": ("✅ <b>Channel link சேமிக்கப்பட்டது!</b>\n\n"
                  "இது இப்போது admin ஒப்புதலுக்காக காத்திருக்கிறது — forward "
                  "தொடங்கும் முன் admin உங்கள் Channel/Community-ல் join செய்ய "
                  "வேண்டும். எப்போது வேண்டுமானாலும் /whatsappstatus மூலம் status "
                  "பார்க்கலாம்."),
        "gujarati": ("✅ <b>Channel link સેવ થઈ ગઈ!</b>\n\n"
                     "તે હવે admin approval ની રાહ જોઈ રહી છે — forwarding શરૂ "
                     "થાય તે પહેલાં admin એ તમારા Channel/Community માં join થવું "
                     "પડશે. ગમે ત્યારે /whatsappstatus થી status જુઓ."),
    },
    "whatsapp_status_none": {
        "en": "📭 You haven't linked a WhatsApp Channel/Community yet. Use /setwhatsapp to add one.",
        "hi": "📭 आपने अभी तक कोई WhatsApp Channel/Community नहीं जोड़ा। जोड़ने के लिए /setwhatsapp इस्तेमाल करें।",
        "hinglish": "📭 Aapne abhi tak koi WhatsApp Channel/Community link nahi kiya. Add karne ke liye /setwhatsapp use karo.",
        "punjabi": "📭 Tusi hun tak koi WhatsApp Channel/Community link nahi kita. Add karan layi /setwhatsapp use karo ji.",
        "haryanvi": "📭 तन्नै अबतक कोए WhatsApp Channel/Community कोनी जोड़ा। जोड़ण खात्तर /setwhatsapp दबा दे।",
        "tamil": "📭 நீங்கள் இன்னும் WhatsApp Channel/Community இணைக்கவில்லை. சேர்க்க /setwhatsapp பயன்படுத்துங்கள்.",
        "gujarati": "📭 તમે હજુ સુધી કોઈ WhatsApp Channel/Community લિંક કર્યું નથી. ઉમેરવા /setwhatsapp નો ઉપયોગ કરો.",
    },
    "whatsapp_status_pending": {
        "en": "⏳ Your WhatsApp Channel is registered and awaiting admin approval.",
        "hi": "⏳ आपका WhatsApp Channel register हो चुका है और admin approval का इंतज़ार है।",
        "hinglish": "⏳ Aapka WhatsApp Channel register ho chuka hai aur admin approval ka wait hai.",
        "punjabi": "⏳ Tuhada WhatsApp Channel register ho chuka hai te admin approval da wait hai ji.",
        "haryanvi": "⏳ थारा WhatsApp Channel रजिस्टर होग्या सै अर admin approval का इंतजार सै।",
        "tamil": "⏳ உங்கள் WhatsApp Channel பதிவு செய்யப்பட்டு admin ஒப்புதலுக்காக காத்திருக்கிறது.",
        "gujarati": "⏳ તમારું WhatsApp Channel register થઈ ગયું છે અને admin approval ની રાહ જોવાઈ રહી છે.",
    },
    "whatsapp_status_active": {
        "en": "✅ Your WhatsApp Channel is active — \"back in stock\" alerts forward there automatically.",
        "hi": "✅ आपका WhatsApp Channel active है — \"back in stock\" alerts वहाँ अपने आप forward होते हैं।",
        "hinglish": "✅ Aapka WhatsApp Channel active hai — \"back in stock\" alerts wahan apne aap forward hote hain.",
        "punjabi": "✅ Tuhada WhatsApp Channel active hai ji — \"back in stock\" alerts othe apne aap forward hunde ne.",
        "haryanvi": "✅ थारा WhatsApp Channel एक्टिव सै — \"back in stock\" alerts ओड़ैभी अपने आप forward होवैं सैं।",
        "tamil": "✅ உங்கள் WhatsApp Channel செயலில் உள்ளது — \"back in stock\" alerts தானாக அங்கு forward ஆகும்.",
        "gujarati": "✅ તમારું WhatsApp Channel active છે — \"back in stock\" alerts ત્યાં આપોઆપ forward થાય છે.",
    },
    "whatsapp_status_disabled": {
        "en": "🚫 Your WhatsApp Channel forwarding has been disabled by the admin. Contact them, or /setwhatsapp a new link.",
        "hi": "🚫 आपका WhatsApp Channel forwarding admin ने बंद कर दिया है। उनसे संपर्क करें, या /setwhatsapp से नया link भेजें।",
        "hinglish": "🚫 Aapka WhatsApp Channel forwarding admin ne band kar diya hai. Unse contact karo, ya /setwhatsapp se naya link bhejo.",
        "punjabi": "🚫 Tuhada WhatsApp Channel forwarding admin ne band kar dita hai. Unhan nu sampark karo, ja /setwhatsapp naal navan link bhejo.",
        "haryanvi": "🚫 थारा WhatsApp Channel forwarding admin नै बंद करदिया सै। उसतै बात कर, या /setwhatsapp तै नया link भेज दे।",
        "tamil": "🚫 உங்கள் WhatsApp Channel forwarding admin ஆல் முடக்கப்பட்டுள்ளது. அவரைத் தொடர்பு கொள்ளுங்கள், அல்லது /setwhatsapp மூலம் புதிய link அனுப்புங்கள்.",
        "gujarati": "🚫 તમારું WhatsApp Channel forwarding admin દ્વારા બંધ કરવામાં આવ્યું છે. તેમનો સંપર્ક કરો, અથવા /setwhatsapp થી નવી link મોકલો.",
    },

    # ── Apple Store pickup-availability tracking (/trackpickup) ───────────────
    "trackpickup_usage": {
        "en": ("📍 <b>Track Apple Store pickup availability</b>\n\n"
               "Usage: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
               "Send the product page URL followed by one or more 6-digit pincodes. "
               "You'll get a notification the moment pickup becomes available at any "
               "nearby store for any of them."),
        "hi": ("📍 <b>Apple Store pickup availability track करें</b>\n\n"
               "इस्तेमाल करें: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
               "Product page का URL भेजें, उसके बाद एक या ज़्यादा 6-digit pincode। "
               "जैसे ही किसी भी pincode के पास किसी store पर pickup available होगा, "
               "आपको notification मिल जाएगा।"),
        "hinglish": ("📍 <b>Apple Store pickup availability track karo</b>\n\n"
                     "Usage: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
                     "Product page ka URL bhejo, uske baad ek ya zyada 6-digit pincode. "
                     "Jaise hi kisi bhi pincode ke paas kisi store pe pickup available "
                     "hoga, notification mil jaayega."),
        "punjabi": ("📍 <b>Apple Store pickup availability track karo ji</b>\n\n"
                    "Usage: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
                    "Product page da URL bhejo, uske baad ek ja zyada 6-digit pincode. "
                    "Jiven hi kise vi pincode de nede kise store te pickup available "
                    "hoyega, notification aa jaayega!"),
        "haryanvi": ("📍 <b>Apple Store pickup availability track कर</b>\n\n"
                     "इस्तेमाल कर: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
                     "Product page का URL भेज, उसकै बाद एक या ज्यादा 6-digit pincode। "
                     "ज्यूं ए कोए भी pincode कै धोरै किसे store पै pickup available "
                     "होज्या, तन्नै notification मिलज्यागी।"),
        "tamil": ("📍 <b>Apple Store pickup availability track செய்யுங்கள்</b>\n\n"
                  "பயன்பாடு: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
                  "Product page-இன் URL-ஐ அனுப்பவும், அதன் பின் ஒன்று அல்லது அதற்கு "
                  "மேற்பட்ட 6-digit pincode-களை அனுப்பவும். எந்த pincode அருகிலும் "
                  "எந்த store-லும் pickup available ஆன உடனேயே உங்களுக்கு notification "
                  "வரும்."),
        "gujarati": ("📍 <b>Apple Store pickup availability track કરો</b>\n\n"
                     "ઉપયોગ: <code>/trackpickup &lt;apple_url&gt; &lt;pincode1&gt; &lt;pincode2&gt; ...</code>\n\n"
                     "Product page નું URL મોકલો, પછી એક અથવા વધુ 6-digit pincode. "
                     "જેવું કોઈપણ pincode ની નજીક કોઈ store પર pickup available થાય, "
                     "તમને તરત notification મળી જશે."),
    },
    "trackpickup_invalid_url": {
        "en": "⚠️ That doesn't look like an Apple Store product URL — pickup tracking only works for apple.com product pages.",
        "hi": "⚠️ ये Apple Store का product URL नहीं लग रहा — pickup tracking सिर्फ apple.com की product pages के लिए काम करता है।",
        "hinglish": "⚠️ Ye Apple Store ka product URL nahi lag raha — pickup tracking sirf apple.com ki product pages ke liye kaam karta hai.",
        "punjabi": "⚠️ Eh Apple Store da product URL nahi lagda ji — pickup tracking sirf apple.com diyan product pages layi kaam karda hai.",
        "haryanvi": "⚠️ ये Apple Store का product URL कोनी लाग्या — pickup tracking सिर्फ apple.com की product pages पै काम करै सै।",
        "tamil": "⚠️ இது Apple Store product URL போல் தெரியவில்லை — pickup tracking apple.com product pages-க்கு மட்டுமே வேலை செய்யும்.",
        "gujarati": "⚠️ આ Apple Store નું product URL લાગતું નથી — pickup tracking ફક્ત apple.com ની product pages માટે જ કામ કરે છે.",
    },
    "trackpickup_invalid_pincode": {
        "en": "⚠️ <code>{pincode}</code> isn't a valid pincode — each one must be exactly 6 digits.",
        "hi": "⚠️ <code>{pincode}</code> valid pincode नहीं है — हर pincode ठीक 6 digits का होना चाहिए।",
        "hinglish": "⚠️ <code>{pincode}</code> valid pincode nahi hai — har pincode exactly 6 digits ka hona chahiye.",
        "punjabi": "⚠️ <code>{pincode}</code> valid pincode nahi hai ji — har pincode bilkul 6 digits da hona chahida hai.",
        "haryanvi": "⚠️ <code>{pincode}</code> सही pincode कोनी — हरेक pincode ठीक 6 digit का होणा चाहिए।",
        "tamil": "⚠️ <code>{pincode}</code> சரியான pincode இல்லை — ஒவ்வொரு pincode-உம் சரியாக 6 digits ஆக இருக்க வேண்டும்.",
        "gujarati": "⚠️ <code>{pincode}</code> માન્ય pincode નથી — દરેક pincode બરાબર 6 digits નું હોવું જોઈએ.",
    },
    "trackpickup_sku_failed": {
        "en": ("⚠️ Couldn't read this product's details from the page — it may not "
               "be a valid product URL, or Apple's page changed. Please double-check "
               "the link and try again."),
        "hi": ("⚠️ इस product की details page से नहीं पढ़ पाए — शायद ये valid product "
               "URL नहीं है, या Apple के page में बदलाव हुआ है। कृपया link दोबारा चेक "
               "करके फिर कोशिश करें।"),
        "hinglish": ("⚠️ Is product ki details page se nahi padh paaye — shayad ye "
                     "valid product URL nahi hai, ya Apple ke page mein change hua "
                     "hai. Please link dobara check karke phir try karo."),
        "punjabi": ("⚠️ Is product diyan details page ton nahi parh sake — shayad eh "
                    "valid product URL nahi hai, ja Apple de page vich tabdeeli ho "
                    "gayi hai. Link dobara check karke fer try karo ji."),
        "haryanvi": ("⚠️ इस product की डिटेल page तै कोनी पढ़ पाया — शायद ये सही product "
                     "URL कोनी सै, या Apple के page म्ह बदलाव होग्या सै। लिंक दोबारा "
                     "चेक करकै फेर ट्राई कर।"),
        "tamil": ("⚠️ இந்த product-இன் விவரங்களை page-இலிருந்து படிக்க முடியவில்லை — இது "
                  "சரியான product URL இல்லாமல் இருக்கலாம், அல்லது Apple-இன் page "
                  "மாறியிருக்கலாம். தயவுசெய்து link-ஐ மீண்டும் சரிபார்த்து முயற்சிக்கவும்."),
        "gujarati": ("⚠️ આ product ની વિગતો page માંથી વાંચી ન શકાયું — કદાચ આ માન્ય "
                     "product URL નથી, અથવા Apple ના page માં ફેરફાર થયો છે. કૃપા કરી "
                     "link ફરી ચેક કરીને પ્રયત્ન કરો."),
    },
    "trackpickup_added": {
        "en": ("✅ <b>Now tracking pickup availability!</b>\n\n"
               "📦 <b>{name}</b>\n"
               "📍 Pincodes: {pincodes}\n\n"
               "You'll get a notification the moment pickup becomes available at any "
               "nearby store for any of these pincodes."),
        "hi": ("✅ <b>Pickup availability track होना शुरू!</b>\n\n"
               "📦 <b>{name}</b>\n"
               "📍 Pincodes: {pincodes}\n\n"
               "इनमें से किसी भी pincode के पास किसी store पर pickup available होते "
               "ही आपको notification मिल जाएगा।"),
        "hinglish": ("✅ <b>Pickup availability track hona shuru!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "📍 Pincodes: {pincodes}\n\n"
                     "In mein se kisi bhi pincode ke paas kisi store pe pickup "
                     "available hote hi notification mil jaayega."),
        "punjabi": ("✅ <b>Pickup availability track honi shuru ho gayi!</b>\n\n"
                    "📦 <b>{name}</b>\n"
                    "📍 Pincodes: {pincodes}\n\n"
                    "Inhan vichon kise vi pincode de nede kise store te pickup "
                    "available hunde hi notification aa jaayega ji!"),
        "haryanvi": ("✅ <b>Pickup availability track होणा शुरू!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "📍 Pincodes: {pincodes}\n\n"
                     "इन म्हसै कोए भी pincode कै धोरै किसे store पै pickup available "
                     "होते ए तन्नै notification मिलज्यागी।"),
        "tamil": ("✅ <b>Pickup availability track ஆரம்பம்!</b>\n\n"
                  "📦 <b>{name}</b>\n"
                  "📍 Pincodes: {pincodes}\n\n"
                  "இவற்றில் எந்த pincode அருகிலும் எந்த store-லும் pickup available "
                  "ஆன உடனேயே உங்களுக்கு notification வரும்."),
        "gujarati": ("✅ <b>Pickup availability track કરવાનું શરૂ!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "📍 Pincodes: {pincodes}\n\n"
                     "આમાંથી કોઈપણ pincode ની નજીક કોઈ store પર pickup available "
                     "થતાં જ તમને notification મળી જશે."),
    },
    "pickup_alert": {
        "en": ("📍 <b>Pickup Available!</b>\n\n"
               "📦 <b>{name}</b>\n"
               "Near pincode <b>{pincode}</b>:\n\n{stores_block}"),
        "hi": ("📍 <b>Pickup Available हो गया!</b>\n\n"
               "📦 <b>{name}</b>\n"
               "Pincode <b>{pincode}</b> के पास:\n\n{stores_block}"),
        "hinglish": ("📍 <b>Pickup Available ho gaya!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "Pincode <b>{pincode}</b> ke paas:\n\n{stores_block}"),
        "punjabi": ("📍 <b>Pickup Available ho gaya ji!</b>\n\n"
                    "📦 <b>{name}</b>\n"
                    "Pincode <b>{pincode}</b> de nede:\n\n{stores_block}"),
        "haryanvi": ("📍 <b>Pickup Available होग्या!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "Pincode <b>{pincode}</b> कै धोरै:\n\n{stores_block}"),
        "tamil": ("📍 <b>Pickup Available ஆகிவிட்டது!</b>\n\n"
                  "📦 <b>{name}</b>\n"
                  "Pincode <b>{pincode}</b> அருகில்:\n\n{stores_block}"),
        "gujarati": ("📍 <b>Pickup Available થયું!</b>\n\n"
                     "📦 <b>{name}</b>\n"
                     "Pincode <b>{pincode}</b> ની નજીક:\n\n{stores_block}"),
    },

    # ── Generic fallback for an unanticipated handler-level exception ─────────
    "unexpected_error": {
        "en": "⚠️ Something went wrong while processing that. Please try again in a moment — if it keeps happening, let the admin know.",
        "hi": "⚠️ इसे process करते समय कुछ गड़बड़ हो गई। कृपया थोड़ी देर बाद फिर कोशिश करें — अगर बार-बार हो रहा है तो admin को बताएं।",
        "hinglish": "⚠️ Isse process karte waqt kuch gadbad ho gayi. Thodi der baad phir try karo — agar baar-baar ho raha hai to admin ko batao.",
        "punjabi": "⚠️ Eh process karde waqt kujh gadbad ho gayi ji. Thodi der baad fer try karo — je vaar-vaar ho reha hai taan admin nu dasso.",
        "haryanvi": "⚠️ ये process करदे टेम कुछ गड़बड़ होगी। थोड़ी देर बाद फेर ट्राई कर — जै बार-बार होरया सै तो admin नै बता दे।",
        "tamil": "⚠️ இதை process செய்யும்போது ஏதோ தவறு ஏற்பட்டது. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும் — தொடர்ந்து நடந்தால் admin-க்குத் தெரியப்படுத்துங்கள்.",
        "gujarati": "⚠️ આ process કરતી વખતે કંઈક ખોટું થયું. થોડી વાર પછી ફરી પ્રયત્ન કરો — જો વારંવાર થાય તો admin ને જણાવો.",
    },

    # ── /mypickups — live, on-demand pickup-availability status ───────────────
    "mypickups_empty": {
        "en": "📭 You have no tracked pickup items yet. Use /trackpickup to add one.",
        "hi": "📭 आपने अभी तक कोई pickup item track नहीं किया। जोड़ने के लिए /trackpickup इस्तेमाल करें।",
        "hinglish": "📭 Aapne abhi tak koi pickup item track nahi kiya. Add karne ke liye /trackpickup use karo.",
        "punjabi": "📭 Tusi hun tak koi pickup item track nahi kita ji. Add karan layi /trackpickup use karo.",
        "haryanvi": "📭 तन्नै अबतक कोए pickup item track कोनी करया। जोड़ण खात्तर /trackpickup दबा दे।",
        "tamil": "📭 நீங்கள் இன்னும் எந்த pickup item-ஐயும் track செய்யவில்லை. சேர்க்க /trackpickup பயன்படுத்துங்கள்.",
        "gujarati": "📭 તમે હજુ સુધી કોઈ pickup item track કર્યું નથી. ઉમેરવા /trackpickup નો ઉપયોગ કરો.",
    },
    "mypickups_checking": {
        "en": "⏳ Checking live pickup availability for {count} tracked item(s)… this may take a moment.",
        "hi": "⏳ {count} tracked item(s) की live pickup availability check हो रही है… थोड़ा समय लग सकता है।",
        "hinglish": "⏳ {count} tracked item(s) ki live pickup availability check ho rahi hai… thoda time lag sakta hai.",
        "punjabi": "⏳ {count} tracked item(s) di live pickup availability check ho rahi hai ji… thoda time lag sakda hai.",
        "haryanvi": "⏳ {count} tracked item(s) की live pickup availability चेक होरही सै… थोड़ा टेम लाग सकै सै।",
        "tamil": "⏳ {count} tracked item(s)-க்கான live pickup availability check செய்யப்படுகிறது… சிறிது நேரம் ஆகலாம்.",
        "gujarati": "⏳ {count} tracked item(s) ની live pickup availability check થઈ રહી છે… થોડો સમય લાગી શકે છે.",
    },
    "mypickups_header": {
        "en": "📍 <b>Your Pickup Tracking — Live Status</b>",
        "hi": "📍 <b>आपकी Pickup Tracking — Live Status</b>",
        "hinglish": "📍 <b>Aapki Pickup Tracking — Live Status</b>",
        "punjabi": "📍 <b>Tuhadi Pickup Tracking — Live Status</b>",
        "haryanvi": "📍 <b>थारी Pickup Tracking — Live Status</b>",
        "tamil": "📍 <b>உங்கள் Pickup Tracking — Live Status</b>",
        "gujarati": "📍 <b>તમારું Pickup Tracking — Live Status</b>",
    },
    "mypickups_line_available": {
        "en": "  ✅ <code>{pincode}</code>: available at {stores}",
        "hi": "  ✅ <code>{pincode}</code>: यहाँ available है — {stores}",
        "hinglish": "  ✅ <code>{pincode}</code>: available hai — {stores}",
        "punjabi": "  ✅ <code>{pincode}</code>: available hai ji — {stores}",
        "haryanvi": "  ✅ <code>{pincode}</code>: उपलब्ध सै — {stores}",
        "tamil": "  ✅ <code>{pincode}</code>: இங்கு available — {stores}",
        "gujarati": "  ✅ <code>{pincode}</code>: અહીં available છે — {stores}",
    },
    "mypickups_line_unavailable": {
        "en": "  ❌ <code>{pincode}</code>: not available right now",
        "hi": "  ❌ <code>{pincode}</code>: अभी available नहीं है",
        "hinglish": "  ❌ <code>{pincode}</code>: abhi available nahi hai",
        "punjabi": "  ❌ <code>{pincode}</code>: hun available nahi hai ji",
        "haryanvi": "  ❌ <code>{pincode}</code>: अबार उपलब्ध कोनी",
        "tamil": "  ❌ <code>{pincode}</code>: இப்போது available இல்லை",
        "gujarati": "  ❌ <code>{pincode}</code>: અત્યારે available નથી",
    },
    "mypickups_line_check_failed": {
        "en": "  ⚠️ <code>{pincode}</code>: check failed — try again shortly",
        "hi": "  ⚠️ <code>{pincode}</code>: check नहीं हो पाया — थोड़ी देर बाद फिर कोशिश करें",
        "hinglish": "  ⚠️ <code>{pincode}</code>: check fail ho gaya — thodi der baad phir try karo",
        "punjabi": "  ⚠️ <code>{pincode}</code>: check fail ho gaya ji — thodi der baad fer try karo",
        "haryanvi": "  ⚠️ <code>{pincode}</code>: चेक कोनी होया — थोड़ी देर बाद फेर ट्राई कर",
        "tamil": "  ⚠️ <code>{pincode}</code>: check தோல்வியடைந்தது — சிறிது நேரம் கழித்து முயற்சிக்கவும்",
        "gujarati": "  ⚠️ <code>{pincode}</code>: check નિષ્ફળ ગયું — થોડી વાર પછી ફરી પ્રયત્ન કરો",
    },

    # ── /untrackpickup — stop tracking a pickup item ───────────────────────────
    "untrackpickup_empty": {
        "en": "📭 You have no tracked pickup items.\nUse /trackpickup to start tracking one!",
        "hi": "📭 आपका कोई tracked pickup item नहीं है।\nShuru karne ke liye /trackpickup इस्तेमाल करें!",
        "hinglish": "📭 Aapka koi tracked pickup item nahi hai.\nShuru karne ke liye /trackpickup use karo!",
        "punjabi": "📭 Tuhada koi tracked pickup item nahi hai ji.\nShuru karan layi /trackpickup use karo!",
        "haryanvi": "📭 थारा कोए tracked pickup item कोनी।\nशुरू करण खात्तर /trackpickup दबा दे!",
        "tamil": "📭 உங்களிடம் tracked pickup item இல்லை.\nதொடங்க /trackpickup பயன்படுத்துங்கள்!",
        "gujarati": "📭 તમારી પાસે કોઈ tracked pickup item નથી.\nશરૂ કરવા /trackpickup નો ઉપયોગ કરો!",
    },
    "untrackpickup_prompt": {
        "en": "🗑 <b>Select a pickup item to stop tracking:</b>",
        "hi": "🗑 <b>Tracking बंद करने के लिए कोई pickup item चुनें:</b>",
        "hinglish": "🗑 <b>Tracking band karne ke liye koi pickup item choose karo:</b>",
        "punjabi": "🗑 <b>Tracking band karan layi koi pickup item choose karo ji:</b>",
        "haryanvi": "🗑 <b>Tracking बंद करण खात्तर कोए pickup item छाँट ले:</b>",
        "tamil": "🗑 <b>Tracking-ஐ நிறுத்த ஒரு pickup item-ஐ தேர்ந்தெடுங்கள்:</b>",
        "gujarati": "🗑 <b>Tracking બંધ કરવા કોઈ pickup item પસંદ કરો:</b>",
    },
}
