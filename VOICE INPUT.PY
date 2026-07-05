import os
import sys
import time

# 🛠️ जरूरी लाइब्रेरीज को इंपोर्ट करना (Try-Except ब्लॉक के साथ ताकि यूजर को एरर न मिले)
try:
    import numpy as np
    import sounddevice as sd
    from scipy.io import wavfile
except ImportError:
    print("🚨 कुछ जरूरी लाइब्रेरीज गायब हैं!")
    print("💡 कृपया अपने टर्मिनल/Pydroid 3 में ये कमांड्स रन करें:")
    print("   pip install numpy sounddevice scipy")
    sys.exit()

class VoiceAuthSystem:
    def _init_(self, storage_dir="voice_db"):
        self.fs = 44100  # 🎙️ Sampling Rate (स्टैंडर्ड ऑडियो क्वालिटी)
        self.duration = 3  # ⏱️ रिकॉर्डिंग का समय (3 सेकंड)
        self.storage_dir = storage_dir
        
        # वॉइस डेटा स्टोर करने के लिए फोल्डर बनाना
        if not os.path.exists(self.storage_dir):
            os.makedirs(self.storage_dir)
            
    def record_audio(self):
        """3 सेकंड के लिए यूजर की आवाज रिकॉर्ड करने का फंक्शन"""
        print(f"\n🎙️ रिकॉर्डिंग शुरू होने वाली है... कृपया शांत रहें।")
        for i in range(3, 0, -1):
            print(f"⏱️ रिकॉर्डिंग {i} सेकंड में शुरू होगी...")
            time.sleep(1)
            
        print("🔴 रिकॉर्डिंग चालू है... बोलना शुरू करें!")
        # sounddevice की मदद से ऑडियो रिकॉर्ड करना (Mono Channel)
        audio = sd.rec(int(self.duration * self.fs), samplerate=self.fs, channels=1, dtype='float32')
        sd.wait()  # रिकॉर्डिंग पूरी होने तक रुकना
        print("🟢 रिकॉर्डिंग समाप्त!")
        return audio

    def extract_voice_signature(self, audio_data):
        """
        ऑडियो डेटा से एक यूनिक सिग्नेचर (Feature) निकालना।
        यह एआई और डेटा साइंस का एक बेसिक रूप है।
        """
        # फ्लैट ऑडियो एरे का एब्सोल्यूट एवरेज और फ्रीक्वेंसी स्पेक्ट्रम का बेसिक्स
        flat_audio = audio_data.flatten()
        
        # सिग्नल को नॉर्मलाइज करना ताकि वॉल्यूम ऊपर-नीचे होने पर भी मैचिंग सही हो
        if np.max(np.abs(flat_audio)) > 0:
            flat_audio = flat_audio / np.max(np.abs(flat_audio))
            
        # आवाज के उतार-चढ़ाव (RMS Energy) को सिग्नेचर की तरह इस्तेमाल करना
        return flat_audio

    def register_user(self, username):
        """यूजर की आवाज रिकॉर्ड करके .npy फाइल में सेव करना"""
        print(f"\n=== 👤 नया वॉइस रजिस्ट्रेशन: {username} ===")
        audio = self.record_audio()
        
        # वॉइस सिग्नेचर निकालना
        voice_signature = self.extract_voice_signature(audio)
        
        # फाइल पाथ सेट करना
        file_path = os.path.join(self.storage_dir, f"{username.lower()}_voice.npy")
        
        # NumPy की मदद से डेटा को बाइनरी (.npy) फॉर्मेट में पक्का लॉक करना
        np.save(file_path, voice_signature)
        print(f"💾 सफलता! {username} का वॉइस प्रोफाइल '{file_path}' में सुरक्षित सेव हो गया है।")

    def verify_user(self, username, threshold=0.15):
        """यूजर की लाइव आवाज लेकर सेव की गई फाइल से मैच करना"""
        print(f"\n=== 🔒 वॉइस ऑथेंटिकेशन: {username} ===")
        file_path = os.path.join(self.storage_dir, f"{username.lower()}_voice.npy")
        
        # चेक करना कि यूजर पहले से रजिस्टर्ड है या नहीं
        if not os.path.exists(file_path):
            print(f"❌ एरर: यूजर '{username}' का कोई वॉइस डेटा नहीं मिला! कृपया पहले रजिस्टर करें।")
            return False
            
        # पहले से सेव डेटा को लोड करना
        saved_signature = np.load(file_path)
        
        # लाइव आवाज रिकॉर्ड करना
        live_audio = self.record_audio()
        live_signature = self.extract_voice_signature(live_audio)
        
        # दोनों वॉइस सिग्नेचर्स के बीच की दूरी (Euclidean Distance) मापना
        # जितना कम स्कोर होगा, आवाज उतनी ही मैच करेगी
        distance = np.linalg.norm(saved_signature - live_signature) / len(saved_signature)
        
        print(f"\n📊 वॉइस मैचिंग स्कोर: {distance:.6f} (थ्रेसहोल्ड सीमा: {threshold})")
        
        if distance < threshold:
            print("🔓 एक्सेस ग्रांटेड! आवाज पूरी तरह मैच हो गई है। वेलकम बॉस!")
            return True
        else:
            print("🔒 एक्सेस डिनाइड! आवाज मैच नहीं हुई।")
            return False

# ---- टेस्ट रन ----
if _name_ == "_main_":
    auth_system = VoiceAuthSystem()
    
    while True:
        print("\n==============================")
        print("  🎙️ VOICE BIOMETRIC SYSTEM  ")
        print("==============================")
        print("1. नया यूजर रजिस्टर करें (Register)")
        print("2. आवाज वेरीफाई करें (Login)")
        print("3. बाहर निकलें (Exit)")
        
        choice = input("\n👉 अपना विकल्प चुनें (1/2/3): ").strip()
        
        if choice == '1':
            name = input("👤 रजिस्टर करने के लिए नाम डालें: ").strip()
            if name:
                auth_system.register_user(name)
        elif choice == '2':
            name = input("👤 लॉगिन करने के लिए नाम डालें: ").strip()
            if name:
                auth_system.verify_user(name)
        elif choice == '3':
            print("सिस्टम बंद हो रहा है। सुरक्षित रहें, बॉस!")
            break
        else:
            print("invalid विकल्प! कृपया दोबारा कोशिश करें।")