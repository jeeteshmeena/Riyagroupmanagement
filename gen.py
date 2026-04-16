import random

hindi_pool = [
    "यार, मैं तुम्हारा दिमाग नहीं पढ़ सकती। सही नाम बताओ। {emoji}",
    "गलत नाम लिखोगे तो गलत ही मिलेगा ना। थोड़ा ध्यान से लिखो! {emoji}",
    "अरे, ये कौन सा नाम हुआ? सही से टाइप करो मैं हूँ ना ढूंढने के लिए। {emoji}",
    "मैं कोई जादूगरनी नहीं हूँ जो बिना सही नाम के सब ढूंढ लाऊं! {emoji}",
    "नाम सही दोगे तभी तो कहानी दे पाउंगी। फिर से ट्राई करो! {emoji}",
    "मुझे परेशान मत करो, सही स्पेलिंग लिखो प्लीज! {emoji}",
    "ये 'नहीं' सुनकर मुझे बहुत बुरा लगता है। सही नाम दे दो ना! {emoji}",
    "भाई, मैं लड़की हूँ, कोई गूगल का सुपर कंप्यूटर थोड़ी! सही नाम लिखो। {emoji}",
    "क्या यार! इतनी सी बात? सही नाम बताओ, मैं सब संभाल लूंगी। {emoji}",
    "तुमने नाम ही गलत दिया है, मेरी क्या गलती? 🤷‍♀️",
    "अरे भई, थोड़ा सा तो दिमाग खुद भी लगा लो। स्पेलिंग चेक करो! {emoji}",
    "मुझे मालूम है तुम कन्फ्यूज्ड हो, पर सही नाम तो दो! {emoji}",
    "ये सब मेरे साथ ही क्यों होता है? सही नाम लिखो ना यार! {emoji}",
    "मैं तो बस लिंक देती हूँ। नाम तो तुम्हें ही सही बताना पड़ेगा! {emoji}",
    "इतनी मेहनत से ढूंढती हूँ, और तुम 'नहीं' बोल देते हो? 🥺",
    "क्या फायदा इतना एडवांस होने का जब तुम नाम ही सही नहीं देते? {emoji}",
    "अच्छा चलो, एक बार और ट्राई करो। 🔁 {emoji}",
    "मैं हमेशा तुम्हारी मदद के लिए हूँ, बस थोड़ा कोऑपरेट करो! {emoji}",
    "अरे बाबा, सही नाम लिखोगे तो 2 सेकंड में दे दूंगी। {emoji}",
    "मैंने 10,000 स्टोरी छान मारी, पर ये नाम नहीं मिला! {emoji}",
    "इतना ड्रामा क्यों? सीधे-सीधे नाम क्यों नहीं लिखते? {emoji}",
    "मैं भी इंसान नहीं हूँ, मशीन हूँ, पर इतना तो समझती हूँ कि कुछ गलत है! {emoji}",
    "ठीक है, माफ किया। अब सही नाम लिखो। 😌",
    "मुझे तो चक्कर आ रहे हैं तुम्हारे नाम देखकर! 😵‍💫",
    "बस एक सही नाम की दूरी पर है तुम्हारी कहानी! {emoji}"
]

hinglish_pool = [
    "Yaar, main tumhara dimaag nahi padh sakti. Sahi naam batao. {emoji}",
    "Galat naam likhoge toh galat hi milega na. Thoda dhyan se likho! {emoji}",
    "Arey, ye kaun sa naam hua? Sahi se type karo main hoon na dhundhne ke liye. {emoji}",
    "Main koi jadugarni nahi hoon jo bina sahi naam ke sab dhundh laaun! {emoji}",
    "Naam sahi doge tabhi toh kahani de paungi. Phir se try karo! {emoji}",
    "Mujhe pareshan mat karo, sahi spelling likho please! {emoji}",
    "Ye 'nahi' sunkar mujhe bahut bura lagta hai. Sahi naam de do na! {emoji}",
    "Maine kaha na, spellings check karo ek baar! {emoji}",
    "Kya yaar! Itni si baat? Sahi naam batao, main sab sambhal lungi. {emoji}",
    "Tumne naam hi galat diya hai, meri kya galti? 🤷‍♀️",
    "Arey bhai, thoda sa toh dimaag khud bhi laga lo. {emoji}",
    "Mujhe malum hai tum confused ho, par sahi naam toh do! {emoji}",
    "Ye sab mere saath hi kyu hota hai? Sahi naam likho na yaar! {emoji}",
    "Main toh bas link deti hoon. Naam toh tumhe hi dhyan rakhna padega! {emoji}",
    "Itni mehnat se dhundhti hoon, aur tum 'nahi' bol dete ho. 🥺",
    "Kyu thaka rahe ho mujhe? Sahi naam hi type kar do bas. {emoji}",
    "Accha chalo, ek baar aur try karo. 🔁 {emoji}",
    "Main humesha available hoon, bas thoda cooperate karo! {emoji}",
    "Arey baba, sahi naam likhoge toh 2 second mein link de dungi. {emoji}",
    "Maine 10,000 story check kar li, par ye naam nahi hai! {emoji}",
    "Itna drama kyu? Sidhe-sidhe naam kyu nahi likhte? {emoji}",
    "Achha theek hai, mujhe maaf karo par ab sahi naam do. {emoji}",
    "Theek hai, maaf kiya. Ab sahi naam likho. 😌",
    "Mujhe toh chakkar aa rahe hain tumhare naam dekhkar! 😵‍💫",
    "Bas ek sahi naam ki doori par hai tumhari kahani! {emoji}"
]

english_pool = [
    "I'm a bot, not a mind reader! Spell it right please. {emoji}",
    "If you type the wrong name, you get the wrong story. Simple! {emoji}",
    "What kind of spelling is that? I can't find anything with that. {emoji}",
    "I'm not a sorceress! I need the exact name to find it for you. {emoji}",
    "Give me the right name and I'll fetch the story. Try again! {emoji}",
    "Don't torture me with typos, please! {emoji}",
    "Hearing 'no' breaks my digital heart. Just give me the right name! {emoji}",
    "I'm a girl doing her best here! Check your spelling. {emoji}",
    "Oh come on! Just tell me the right name, I'll take care of the rest. {emoji}",
    "You gave me the wrong name, how is that my fault? 🤷‍♀️",
    "Use a little bit of your brain too! Check the spelling. {emoji}",
    "I know you're confused, but I need the correct name to help! {emoji}",
    "Why does this always happen to me? Just type the right name! {emoji}",
    "I just provide the links. Generating the right name is on you! {emoji}",
    "I search so hard and you just say 'no'? 🥺",
    "What's the point of being advanced if you can't spell? {emoji}",
    "Alright, let's try this one more time. 🔁 {emoji}",
    "I'm always here to help, just cooperate a little! {emoji}",
    "If you just write the correct name, I'll give it to you in 2 seconds. {emoji}",
    "I searched through 10,000 stories and couldn't find this. {emoji}",
    "Why all the drama? Just write the name properly! {emoji}",
    "A little typing accuracy goes a long way. {emoji}",
    "Alright, I'll let it slide. Now give me the right name. 😌",
    "Your spellings are literally making me dizzy! 😵‍💫",
    "You're just one correct spelling away from your story! {emoji}"
]

fonts = [
    lambda t: f"<code>{t}</code>",
    lambda t: f"<b><i>{t}</i></b>",
    lambda t: f"<i>{t}</i>",
    lambda t: f"<b>{t}</b>",
]

emojis = ["✧", "🌸", "✨", "🎀", "🦋", "🫧", "🧚‍♀️", "💖", "💅", "🌺", "💫", "💭", "🌙"]
requests_hint = "\n✸ <i>Try /request if it's not here!</i> 📩"

all_replies = []
all_pools = hindi_pool + hinglish_pool + english_pool
for i in range(100):
    base = random.choice(all_pools)
    emoji = random.choice(emojis)
    text = base.format(emoji=emoji)
    font_func = random.choice(fonts)
    out = font_func(text)
    if random.random() < 0.2:
        out += requests_hint
    all_replies.append(out)

thanks_pool = [
    # Hindi
    "हमेशा स्वागत है! जब भी कोई स्टोरी चाहिए, बस नाम लिखो। {emoji}",
    "कोई बात नहीं! Riya हमेशा यहाँ है आपकी मदद के लिए। {emoji}",
    "बहुत बहुत स्वागत है! अगली स्टोरी के लिए बस नाम टाइप करो। {emoji}",
    "आपकी सेवा में सदा तत्पर! बस नाम लिखो, लिंक हाज़िर। {emoji}",
    "शुक्रिया कहने की ज़रूरत नहीं, मुझे अच्छा लगा! {emoji}",
    # Hinglish
    "Hamesha swagat hai! Aur stories padhni ho toh name type karo. {emoji}",
    "Mention not! Riya is always here. {emoji}",
    "Koi baat nahi! Yahi mera kaam hai. Aur kuch chahiye toh batao. {emoji}",
    "No prob! Next story search karo, main ready hoon. {emoji}",
    "Anytime, seriously. Riya is here 24/7. {emoji}",
    # English
    "Always happy to help! Just drop the name for the next story. {emoji}",
    "You're welcome! Keep reading, keep enjoying. {emoji}",
    "Glad I could help. Just ask anytime! {emoji}",
    "No worries at all! Let me know if you need another one. {emoji}",
    "My pleasure! Let's find some more stories together. {emoji}"
]

all_thanks_replies = []
for i in range(30):
    base = random.choice(thanks_pool)
    emoji = random.choice(emojis)
    text = base.format(emoji=emoji)
    font_func = random.choice(fonts)
    out = font_func(text)
    all_thanks_replies.append(out)

with open('generated_replies.txt', 'w', encoding='utf-8') as f:
    f.write("_NAHI_REPLIES = [\n")
    for r in all_replies:
        f.write(f'    """{r}""",\n')
    f.write("]\n\n")
    
    f.write("_THANKS_REPLIES = [\n")
    for r in all_thanks_replies:
        f.write(f'    """{r}""",\n')
    f.write("]\n")

