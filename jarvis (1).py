import sqlite3
import sys
import time
import os

class JarvisCore:
    def __init__(self, boss_name):
        self.is_running = True
        self.active_mode = False 
        
        # 💾 डेटाबेस कनेक्ट और सेटअप करना
        self.setup_database()
        
        # 💾 डेटाबेस से बॉस का नाम लोड करना
        self.boss = self.load_boss_name(boss_name)
        
        self.speak(f"System Online. Welcome back, {self.boss}. मेमोरी स्टोरेज फिक्स एक्टिवेटेड।")

    def setup_database(self):
        """डेटाबेस और टेबल्स बनाने का फंक्शन - स्टोरेज एरर फिक्स के साथ"""
        try:
            # यह फाइल तुम्हारे फोन के उसी फोल्डर में बनेगी जहाँ यह कोड सेव है
            self.conn = sqlite3.connect("jarvis_memory.db")
            self.cursor = self.conn.cursor()
            
            # टेबल 1: बॉस की प्रोफाइल
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS boss_profile (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """)
            
            # टेबल 2: बॉस की खुद की लिखी ओरिजिनल शायरियां
            self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS boss_shayari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                date_added TEXT
            )
            """)
            
            # डिफॉल्ट प्रोफाइल डेटा डालना और तुरंत COMMIT (सेव) करना
            self.cursor.execute("INSERT OR IGNORE INTO boss_profile (key, value) VALUES ('name', 'Boss')")
            self.cursor.execute("INSERT OR IGNORE INTO boss_profile (key, value) VALUES ('college', 'Silicon University')")
            self.cursor.execute("INSERT OR IGNORE INTO boss_profile (key, value) VALUES ('fav_music', 'Sad Love Feel & Romantic')")
            
            self.conn.commit() # 🔒 ये लाइन डेटा को फोन की मेमोरी में पक्का लॉक कर देती है
            
        except sqlite3.OperationalError:
            print("🚨 जार्विस एरर: फोन की इंटरनल मेमोरी में फाइल सेव करने की परमिशन नहीं है!")
            print("💡 उपाय: अपने Pydroid 3 ऐप की Settings में जाकर 'Storage Permission' को Allow करें।")

    def load_boss_name(self, default_name):
        """डेटाबेस से बॉस का नाम निकालना"""
        try:
            self.cursor.execute("SELECT value FROM boss_profile WHERE key='name'")
            result = self.cursor.fetchone()
            if result:
                return result[0]
            return default_name
        except:
            return default_name

    def speak(self, text):
        """जार्विस का आउटपुट"""
        print(f"🤖 Jarvis: {text}")

    def listen_command(self):
        """बॉस का इनपुट लेने का फंक्शन"""
        try:
            if self.active_mode:
                command = input(f"\n⚡ Jarvis (Listening for Orders): ")
            else:
                command = input(f"\n🎙️ Speak/Type (Boss): ")
            return command.strip().lower()
        except KeyboardInterrupt:
            self.shutdown()

    def ask_permission(self, action_name):
        """🚨 FEATURE 06: NO AUTO-EXECUTION PROTOCOL"""
        self.speak(f"बॉस, क्या मैं यह काम करूँ: [{action_name}]? (Yes/No)")
        permission = input(f"👤 {self.boss}: ").strip().lower()
        
        if permission in ['yes', 'y', 'haan', 'ha']:
            return True
        else:
            self.speak("कमांड कैंसिल कर दिया गया है, बॉस।")
            return False

    def process_core(self, query):
        if not query:
            return

        # -----------------------------------------------------------
        # केस 1: अगर जार्विस पहले से ACTIVE MODE में है
        # -----------------------------------------------------------
        if self.active_mode:
            if "bas" in query or "stop" in query or "thank you" in query or "off" in query:
                self.speak("जी बॉस, एक्टिव मोड ऑफ कर रहा हूँ। अब मैं Standby पर हूँ।")
                self.active_mode = False
                return

            if "exit" in query or "shutdown" in query:
                self.shutdown()
                
            elif "coding" in query or "code" in query or "program" in query:
                self.speak("कोडिंग मोड ऑन है बॉस। C या Python का कौन सा लॉजिक चेक करना है?")
                
            elif "college" in query or "university" in query or "silicon" in query:
                self.cursor.execute("SELECT value FROM boss_profile WHERE key='college'")
                college = self.cursor.fetchone()[0]
                self.speak(f"आपकी प्रोफाइल के हिसाब से आप {college} (Bhubaneswar) की तैयारी कर रहे हैं।")
                
            elif "music" in query or "gaana" in query or "song" in query:
                self.cursor.execute("SELECT value FROM boss_profile WHERE key='fav_music'")
                music = self.cursor.fetchone()[0]
                self.speak(f"बॉस, आपका मूड ट्रैक '{music}' है। 'Main Tenu Samjhawan' या 'Tum Jo Aaye' प्ले करूँ?")
                
            # ✍️ न्यू शायरी ऐड करने का फीचर (With Strict Commit)
            elif "add shayari" in query or "shayari likho" in query:
                self.speak("जी बॉस, अपनी नई शायरी यहाँ लिखिए, मैं इसे डेटाबेस में हमेशा के लिए सेफ कर लूंगा:")
                new_shayari = input(f"📝 Write Shayari (Boss): ")
                if new_shayari:
                    current_date = time.strftime('%Y-%m-%d %H:%M:%S')
                    self.cursor.execute("INSERT INTO boss_shayari (text, date_added) VALUES (?, ?)", (new_shayari, current_date))
                    
                    self.conn.commit() # 🔒 तुरंत मेमोरी में पक्का सेव करो ताकि गायब न हो!
                    self.speak("💾 बहुत खूब बॉस! आपकी शायरी को मैंने अपनी SQL मेमोरी में सुरक्षित सेव कर लिया है।")
                
            # 📖 पुरानी शायरियां पढ़ने का फीचर
            elif "shayari" in query or "mood kharab" in query or "shayar" in query or "odia re" in query:
                self.cursor.execute("SELECT text FROM boss_shayari ORDER BY RANDOM() LIMIT 1")
                row = self.cursor.fetchone()
                
                if row:
                    self.speak(f"आपकी डायरी से एक बेहतरीन शायरी अर्ज है बॉस: '{row[0]}'")
                else:
                    self.speak("अर्ज किया है... 'हौसले के tarकश में कोशिश का वो तीर ज़िंदा रख, हार जा चाहे ज़िंदगी में सब कुछ, मगर फिर से जीतने की उम्मीद ज़िंदा रख।'")
                    
            elif "misao" in query or "katha" in query or "kauthi" in query:
                self.speak("मूँ ओड़िआ बुझी पारुछि बॉस! आप जब चाहें ओड़िया में भी कमांड दे सकते हैं।")
                
            else:
                self.speak(f"बॉस, आपका हुकुम सिर आँखों पर, पर अभी मुझे '{query}' करना सिखाया नहीं गया है।")
            
            return

        # -----------------------------------------------------------
        # केस 2: अगर जार्विस NORMAL MODE में है
        # -----------------------------------------------------------
        if not self.ask_permission(f"कमांड '{query}' को प्रोसेस करना"):
            return

        if "boss" in query or "jarvis" in query or "hello" in query:
            self.speak("जी बॉस, हुकुम कीजिए! मैं अब एक्टिव मोड में हूँ, सीधे कमांड्स दीजिए।")
            self.active_mode = True 
            
        elif "exit" in query or "shutdown" in query:
            self.shutdown()
            
        else:
            self.speak(f"कमांड '{query}' की अनुमति तो मिल गई, पर इसका कोर फंक्शन अभी स्टैंडबाय पर है।")

    def shutdown(self):
        self.speak("डेटाबेस कनेक्शन सुरक्षित बंद हो रहा है...")
        self.conn.close()
        self.speak("कोर सिस्टम बंद हो रहा है। टेक केयर, बॉस।")
        self.is_running = False
        sys.exit()

# ---- जार्विस को रन करना ----
if __name__ == "__main__":
    jarvis = JarvisCore(boss_name="Boss")
    
    while jarvis.is_running:
        user_query = jarvis.listen_command()
        jarvis.process_core(user_query)
