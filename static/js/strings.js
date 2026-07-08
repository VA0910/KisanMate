/**
 * Single source of truth for every UI string, keyed by language.
 * Adding a language = adding one more top-level key here (plus a locale
 * tag and a language-picker card) -- nothing else in the app changes.
 */
(function (global) {
  "use strict";

  var LANGUAGES = [
    { code: "en", label: "English" },
    { code: "hi", label: "हिंदी" },
    { code: "te", label: "తెలుగు" }
  ];

  // BCP-47 tags used for both SpeechRecognition.lang and SpeechSynthesisUtterance.lang.
  var LOCALE_TAGS = { en: "en-IN", hi: "hi-IN", te: "te-IN" };

  // Shown on the language picker itself, before we know which language to use.
  var CHOOSE_LANGUAGE_HEADING = ["Choose your language", "अपनी भाषा चुनें", "మీ భాషను ఎంచుకోండి"];

  var STRINGS = {
    en: {
      appName: "KisanMate",
      tagline: "Your farm helper",
      skipToContent: "Skip to main content",
      changeLanguage: "Change language",
      back: "Back",
      navHome: "Home",
      navDiagnose: "Diagnose",
      navGrow: "Grow",
      navAlerts: "Alerts",
      retry: "Try again",
      cancel: "Cancel",
      loading: "Loading...",
      friendlyErrorGeneric: "Something went wrong. Please try again.",
      friendlyErrorSlow: "That's taking longer than usual. Please check your connection and try again.",

      homeGreeting: "Welcome",
      tapAndSpeak: "Tap and speak",
      listening: "Listening...",
      micUnavailableHint: "Voice isn't available on this device. Use the buttons below.",
      micPermissionDenied: "Please allow microphone access, or use the buttons below.",
      voiceNotUnderstood: "Sorry, I didn't catch that. Please try again or use the buttons below.",
      diagnoseCardTitle: "Diagnose my crop",
      diagnoseCardSub: "Take a photo of your plant",
      recommendCardTitle: "What should I grow?",
      recommendCardSub: "Get crop suggestions",
      voiceOpeningDiagnose: "Opening crop diagnosis",
      voiceOpeningRecommend: "Opening crop suggestions",
      voiceOpeningAlerts: "Opening your alerts",

      diagnoseInstruction: "Take or choose a clear photo of the leaf",
      addPhoto: "Add Photo",
      photoPreviewAlt: "Photo you selected",
      changePhoto: "Choose a different photo",
      submitDiagnose: "Check my crop",
      thinking: "Thinking...",
      thinkingSub: "This can take a few seconds",
      confidenceLow: "Low",
      confidenceMedium: "Medium",
      confidenceHigh: "High",
      confidenceLabel: "How sure we are",
      adviceLabel: "What to do",
      statusAdvise: "We found this",
      statusEscalate: "Needs a closer look",
      playAudio: "Play",
      newPhoto: "New Photo",
      notRight: "This isn't right",
      disputeConfirmQuestion: "Send this to your local RSK officer for a second look?",
      disputeThanks: "Thank you. We've told your local RSK officer to take a look.",
      diagnoseError: "We couldn't check your photo right now. Please try again.",

      recommendInstruction: "Tell us about your field",
      soilLabel: "Soil type",
      zoneLabel: "Your area",
      rainfallLabel: "Rainfall",
      groundwaterLabel: "Water depth",
      chooseAllHint: "Please choose one option in each group",
      showRecommendations: "Show suggestions",
      bestMatch: "Best match",
      startOver: "Start over",
      recommendResultIntro: "Here are good crops for your field",
      recommendError: "We couldn't get suggestions right now. Please try again.",
      recommendVoiceSummary: function (crop) { return crop + " is your best match"; },

      alertsTitle: "Your Alerts",
      alertsEmpty: "No alerts right now. We'll let you know if there's a risk near your farm.",
      alertsLoadError: "We couldn't load your alerts. Please check your connection.",
      inYourArea: "in your area",
      withinKm: function (km) { return "within " + km + " km"; },
      today: "Today",
      yesterday: "Yesterday",
      daysAgo: function (n) { return n + " days ago"; },
      alertsSummarySpoken: function (n) {
        return n === 1 ? "You have 1 new alert" : "You have " + n + " new alerts";
      },

      conditions: {
        late_blight: "Late Blight",
        early_blight: "Early Blight",
        nitrogen_deficiency: "Low Nitrogen",
        other: "Other Issue",
        healthy: "Healthy"
      },
      crops: {
        rice: "Rice", tomato: "Tomato", chili: "Chili",
        cotton: "Cotton", groundnut: "Groundnut", maize: "Maize"
      },
      reasons: {
        high_rainfall_shallow_water_suits_paddy: "Good rain and shallow water suit this crop",
        moderate_water_well_drained_soil_suits_tomato: "Well-drained soil suits this crop",
        well_drained_soil_moderate_rainfall_suits_chili: "Well-drained soil and moderate rain suit this crop",
        black_soil_semi_arid_zone_suits_cotton: "Black soil in a dry area suits this crop",
        low_water_need_suits_deep_groundwater_zones: "Needs little water, good for your area",
        adaptable_crop_moderate_rainfall_suits_maize: "An easy crop that grows well with moderate rain"
      },
      soils: {
        alluvial: "Alluvial (river) soil", black: "Black soil", loamy: "Loamy soil",
        red: "Red soil", sandy: "Sandy soil"
      },
      zones: { delta: "Delta", coastal: "Coastal", upland: "Upland", semi_arid: "Dry area" },
      rainfall: { low: "Low rain", medium: "Medium rain", high: "High rain" },
      groundwater: { shallow: "Shallow", medium: "Medium", deep: "Deep" },
      tiers: { watch: "Watch", warning: "Warning", alert: "Alert" },

      runDemo: "Run demo scenario",
      demoLoading: "Starting the demo…",
      demoError: "The demo needs the database. Please try again.",
      demoNext: "Next",
      demoExit: "Exit demo",
      demoReplay: "Replay",
      demoStepOf: function (n, total) { return "Step " + n + " of " + total; },
      demoStep1: "Meet Ramesh, a tomato farmer near Guntur. KisanMate checks his soil, water and rainfall, and recommends the best crops for his land — tomato is a strong match.",
      demoStep2: "Ramesh photographs a sick leaf. The camera alone leans towards early blight, but the cool, wet weather points to late blight — a contagious disease. Because they disagree, KisanMate does not guess: it escalates to a human expert.",
      demoStep3: "An RSK officer reviews the photo and confirms late blight. The officer's verdict is final — it overrides the AI.",
      demoStep4: "A confirmed contagious case triggers a community alert. Lakshmi, a tomato farmer 3 km away, is warned in her own language, Telugu. Venkat next door grows rice, so he is left undisturbed."
    },

    hi: {
      appName: "KisanMate",
      tagline: "आपकी खेती में मदद",
      skipToContent: "मुख्य सामग्री पर जाएं",
      changeLanguage: "भाषा बदलें",
      back: "पीछे",
      navHome: "होम",
      navDiagnose: "जांच",
      navGrow: "उगाएं",
      navAlerts: "सूचना",
      retry: "फिर से कोशिश करें",
      cancel: "रद्द करें",
      loading: "लोड हो रहा है...",
      friendlyErrorGeneric: "कुछ गड़बड़ हो गई। कृपया फिर से कोशिश करें।",
      friendlyErrorSlow: "इसमें सामान्य से ज़्यादा समय लग रहा है। कृपया इंटरनेट जांचें और फिर से कोशिश करें।",

      homeGreeting: "नमस्ते",
      tapAndSpeak: "बोलने के लिए दबाएं",
      listening: "सुन रहे हैं...",
      micUnavailableHint: "इस फ़ोन में आवाज़ की सुविधा नहीं है। नीचे दिए बटन इस्तेमाल करें।",
      micPermissionDenied: "कृपया माइक की अनुमति दें, या नीचे दिए बटन इस्तेमाल करें।",
      voiceNotUnderstood: "माफ़ करें, समझ नहीं आया। फिर से बोलें या नीचे दिए बटन इस्तेमाल करें।",
      diagnoseCardTitle: "फ़सल की जांच करें",
      diagnoseCardSub: "पौधे की फोटो लें",
      recommendCardTitle: "क्या उगाएं?",
      recommendCardSub: "फ़सल के सुझाव पाएं",
      voiceOpeningDiagnose: "फ़सल जांच खोल रहे हैं",
      voiceOpeningRecommend: "फ़सल सुझाव खोल रहे हैं",
      voiceOpeningAlerts: "आपकी सूचनाएं खोल रहे हैं",

      diagnoseInstruction: "पत्ते की साफ फोटो लें या चुनें",
      addPhoto: "फोटो जोड़ें",
      photoPreviewAlt: "आपकी चुनी हुई फोटो",
      changePhoto: "दूसरी फोटो चुनें",
      submitDiagnose: "मेरी फ़सल जांचें",
      thinking: "सोच रहे हैं...",
      thinkingSub: "इसमें कुछ पल लग सकते हैं",
      confidenceLow: "कम",
      confidenceMedium: "मध्यम",
      confidenceHigh: "अधिक",
      confidenceLabel: "हमें कितना यकीन है",
      adviceLabel: "क्या करें",
      statusAdvise: "हमें यह मिला",
      statusEscalate: "और जांच ज़रूरी है",
      playAudio: "सुनें",
      newPhoto: "नई फोटो",
      notRight: "यह सही नहीं है",
      disputeConfirmQuestion: "क्या इसे अपने नज़दीकी RSK अधिकारी को दोबारा जांच के लिए भेजें?",
      disputeThanks: "धन्यवाद। हमने आपके नज़दीकी RSK अधिकारी को बता दिया है।",
      diagnoseError: "अभी हम आपकी फोटो जांच नहीं पाए। कृपया फिर से कोशिश करें।",

      recommendInstruction: "अपने खेत के बारे में बताएं",
      soilLabel: "मिट्टी का प्रकार",
      zoneLabel: "आपका इलाका",
      rainfallLabel: "बारिश",
      groundwaterLabel: "पानी की गहराई",
      chooseAllHint: "कृपया हर समूह में एक विकल्प चुनें",
      showRecommendations: "सुझाव देखें",
      bestMatch: "सबसे अच्छा विकल्प",
      startOver: "फिर से शुरू करें",
      recommendResultIntro: "आपके खेत के लिए अच्छी फ़सलें",
      recommendError: "अभी सुझाव नहीं मिल पाए। कृपया फिर से कोशिश करें।",
      recommendVoiceSummary: function (crop) { return crop + " आपके लिए सबसे अच्छा विकल्प है"; },

      alertsTitle: "आपकी सूचनाएं",
      alertsEmpty: "अभी कोई सूचना नहीं है। खेत के पास खतरा होने पर हम आपको बताएंगे।",
      alertsLoadError: "सूचनाएं लोड नहीं हो पाईं। कृपया इंटरनेट जांचें।",
      inYourArea: "आपके इलाके में",
      withinKm: function (km) { return km + " किमी के अंदर"; },
      today: "आज",
      yesterday: "कल",
      daysAgo: function (n) { return n + " दिन पहले"; },
      alertsSummarySpoken: function (n) {
        return n === 1 ? "आपके लिए 1 नई सूचना है" : "आपके लिए " + n + " नई सूचनाएं हैं";
      },

      conditions: {
        late_blight: "पछेती झुलसा (Late Blight)",
        early_blight: "अगेती झुलसा (Early Blight)",
        nitrogen_deficiency: "नाइट्रोजन की कमी",
        other: "अन्य समस्या",
        healthy: "स्वस्थ फ़सल"
      },
      crops: {
        rice: "धान", tomato: "टमाटर", chili: "मिर्च",
        cotton: "कपास", groundnut: "मूंगफली", maize: "मक्का"
      },
      reasons: {
        high_rainfall_shallow_water_suits_paddy: "अच्छी बारिश और कम गहरे पानी में यह फ़सल अच्छी होती है",
        moderate_water_well_drained_soil_suits_tomato: "अच्छे जल निकास वाली मिट्टी में यह फ़सल अच्छी होती है",
        well_drained_soil_moderate_rainfall_suits_chili: "अच्छे जल निकास और मध्यम बारिश में यह फ़सल अच्छी होती है",
        black_soil_semi_arid_zone_suits_cotton: "सूखे इलाके की काली मिट्टी में यह फ़सल अच्छी होती है",
        low_water_need_suits_deep_groundwater_zones: "इसे कम पानी चाहिए, आपके इलाके के लिए अच्छी है",
        adaptable_crop_moderate_rainfall_suits_maize: "यह आसान फ़सल है, मध्यम बारिश में अच्छी होती है"
      },
      soils: {
        alluvial: "जलोढ़ (नदी की) मिट्टी", black: "काली मिट्टी", loamy: "दोमट मिट्टी",
        red: "लाल मिट्टी", sandy: "रेतीली मिट्टी"
      },
      zones: { delta: "डेल्टा क्षेत्र", coastal: "तटीय क्षेत्र", upland: "ऊंचा इलाका", semi_arid: "सूखा इलाका" },
      rainfall: { low: "कम बारिश", medium: "मध्यम बारिश", high: "ज़्यादा बारिश" },
      groundwater: { shallow: "कम गहरा", medium: "मध्यम", deep: "ज़्यादा गहरा" },
      tiers: { watch: "नज़र रखें", warning: "चेतावनी", alert: "खतरा" },

      runDemo: "डेमो चलाएं",
      demoLoading: "डेमो शुरू हो रहा है…",
      demoError: "डेमो के लिए डेटाबेस ज़रूरी है। कृपया फिर से कोशिश करें।",
      demoNext: "आगे",
      demoExit: "डेमो बंद करें",
      demoReplay: "फिर से चलाएं",
      demoStepOf: function (n, total) { return "चरण " + n + " / " + total; },
      demoStep1: "मिलिए रमेश से, गुंटूर के पास एक टमाटर किसान। KisanMate उनकी मिट्टी, पानी और बारिश देखकर सबसे अच्छी फ़सलें सुझाता है — टमाटर उनकी ज़मीन के लिए बढ़िया है।",
      demoStep2: "रमेश एक बीमार पत्ते की फोटो लेते हैं। कैमरा अगेती झुलसा की ओर झुकता है, पर ठंडा-नम मौसम पछेती झुलसा बताता है — जो एक फैलने वाली बीमारी है। मतभेद होने पर KisanMate अंदाज़ा नहीं लगाता: यह मामला विशेषज्ञ को भेज देता है।",
      demoStep3: "एक RSK अधिकारी फोटो देखकर पछेती झुलसा की पुष्टि करते हैं। अधिकारी का फ़ैसला अंतिम है — यह AI से ऊपर है।",
      demoStep4: "पुष्टि हुई फैलने वाली बीमारी एक सामुदायिक चेतावनी शुरू करती है। 3 किमी दूर टमाटर किसान लक्ष्मी को उनकी भाषा तेलुगु में चेतावनी मिलती है। पड़ोस के वेंकट धान उगाते हैं, इसलिए उन्हें परेशान नहीं किया जाता।"
    },

    te: {
      appName: "KisanMate",
      tagline: "మీ వ్యవసాయ సహాయకుడు",
      skipToContent: "ప్రధాన విషయానికి వెళ్ళండి",
      changeLanguage: "భాష మార్చండి",
      back: "వెనుకకు",
      navHome: "హోమ్",
      navDiagnose: "పరీక్ష",
      navGrow: "పంట",
      navAlerts: "హెచ్చరికలు",
      retry: "మళ్ళీ ప్రయత్నించండి",
      cancel: "రద్దు చేయండి",
      loading: "లోడ్ అవుతోంది...",
      friendlyErrorGeneric: "ఏదో తప్పు జరిగింది. దయచేసి మళ్ళీ ప్రయత్నించండి.",
      friendlyErrorSlow: "సాధారణం కంటే ఎక్కువ సమయం పడుతోంది. దయచేసి ఇంటర్నెట్ చూసి మళ్ళీ ప్రయత్నించండి.",

      homeGreeting: "నమస్కారం",
      tapAndSpeak: "మాట్లాడటానికి నొక్కండి",
      listening: "వింటున్నాం...",
      micUnavailableHint: "ఈ ఫోన్‌లో వాయిస్ సదుపాయం లేదు. కింద ఉన్న బటన్లు వాడండి.",
      micPermissionDenied: "దయచేసి మైక్‌కి అనుమతి ఇవ్వండి, లేదా కింద ఉన్న బటన్లు వాడండి.",
      voiceNotUnderstood: "క్షమించండి, అర్థం కాలేదు. మళ్ళీ చెప్పండి లేదా కింద ఉన్న బటన్లు వాడండి.",
      diagnoseCardTitle: "పంటను పరీక్షించండి",
      diagnoseCardSub: "మొక్క ఫోటో తీయండి",
      recommendCardTitle: "ఏమి పండించాలి?",
      recommendCardSub: "పంట సలహాలు పొందండి",
      voiceOpeningDiagnose: "పంట పరీక్ష తెరుస్తున్నాం",
      voiceOpeningRecommend: "పంట సలహాలు తెరుస్తున్నాం",
      voiceOpeningAlerts: "మీ హెచ్చరికలు తెరుస్తున్నాం",

      diagnoseInstruction: "ఆకు యొక్క స్పష్టమైన ఫోటో తీయండి లేదా ఎంచుకోండి",
      addPhoto: "ఫోటో జోడించండి",
      photoPreviewAlt: "మీరు ఎంచుకున్న ఫోటో",
      changePhoto: "వేరే ఫోటో ఎంచుకోండి",
      submitDiagnose: "నా పంటను పరీక్షించండి",
      thinking: "ఆలోచిస్తున్నాం...",
      thinkingSub: "దీనికి కొన్ని క్షణాలు పట్టవచ్చు",
      confidenceLow: "తక్కువ",
      confidenceMedium: "మధ్యస్థం",
      confidenceHigh: "ఎక్కువ",
      confidenceLabel: "మాకు ఎంత నమ్మకం ఉంది",
      adviceLabel: "ఏమి చేయాలి",
      statusAdvise: "మాకు ఇది కనిపించింది",
      statusEscalate: "మరింత పరిశీలన అవసరం",
      playAudio: "వినండి",
      newPhoto: "కొత్త ఫోటో",
      notRight: "ఇది సరైనది కాదు",
      disputeConfirmQuestion: "దీన్ని మీ సమీప RSK అధికారికి మళ్ళీ చూడటానికి పంపాలా?",
      disputeThanks: "ధన్యవాదాలు. మేము మీ సమీప RSK అధికారికి తెలియజేశాము.",
      diagnoseError: "ప్రస్తుతం మేము మీ ఫోటోను పరీక్షించలేకపోయాము. దయచేసి మళ్ళీ ప్రయత్నించండి.",

      recommendInstruction: "మీ పొలం గురించి చెప్పండి",
      soilLabel: "నేల రకం",
      zoneLabel: "మీ ప్రాంతం",
      rainfallLabel: "వర్షపాతం",
      groundwaterLabel: "నీటి లోతు",
      chooseAllHint: "దయచేసి ప్రతి గుంపులో ఒక ఎంపిక చేయండి",
      showRecommendations: "సలహాలు చూడండి",
      bestMatch: "ఉత్తమ ఎంపిక",
      startOver: "మళ్ళీ మొదలు పెట్టండి",
      recommendResultIntro: "మీ పొలానికి మంచి పంటలు",
      recommendError: "ప్రస్తుతం సలహాలు పొందలేకపోయాము. దయచేసి మళ్ళీ ప్రయత్నించండి.",
      recommendVoiceSummary: function (crop) { return crop + " మీకు ఉత్తమమైనది"; },

      alertsTitle: "మీ హెచ్చరికలు",
      alertsEmpty: "ప్రస్తుతం హెచ్చరికలు లేవు. మీ పొలం దగ్గర ప్రమాదం ఉంటే మేము మీకు తెలియజేస్తాము.",
      alertsLoadError: "హెచ్చరికలు లోడ్ కాలేదు. దయచేసి ఇంటర్నెట్ చూడండి.",
      inYourArea: "మీ ప్రాంతంలో",
      withinKm: function (km) { return km + " కి.మీ. లోపల"; },
      today: "ఈరోజు",
      yesterday: "నిన్న",
      daysAgo: function (n) { return n + " రోజుల క్రితం"; },
      alertsSummarySpoken: function (n) {
        return n === 1 ? "మీకు 1 కొత్త హెచ్చరిక ఉంది" : "మీకు " + n + " కొత్త హెచ్చరికలు ఉన్నాయి";
      },

      conditions: {
        late_blight: "ఆలస్య తెగులు (Late Blight)",
        early_blight: "త్వరిత తెగులు (Early Blight)",
        nitrogen_deficiency: "నత్రజని లోపం",
        other: "ఇతర సమస్య",
        healthy: "ఆరోగ్యకరమైన పంట"
      },
      crops: {
        rice: "వరి", tomato: "టమాటా", chili: "మిర్చి",
        cotton: "పత్తి", groundnut: "వేరుశనగ", maize: "మొక్కజొన్న"
      },
      reasons: {
        high_rainfall_shallow_water_suits_paddy: "మంచి వర్షం మరియు తక్కువ లోతు నీరు ఈ పంటకు బాగుంటుంది",
        moderate_water_well_drained_soil_suits_tomato: "నీరు త్వరగా ఇంకే నేలలో ఈ పంట బాగా పండుతుంది",
        well_drained_soil_moderate_rainfall_suits_chili: "నీరు ఇంకే నేల మరియు మధ్యస్థ వర్షంలో ఈ పంట బాగుంటుంది",
        black_soil_semi_arid_zone_suits_cotton: "పొడి ప్రాంతంలోని నల్ల నేలలో ఈ పంట బాగుంటుంది",
        low_water_need_suits_deep_groundwater_zones: "దీనికి నీరు తక్కువ కావాలి, మీ ప్రాంతానికి బాగుంటుంది",
        adaptable_crop_moderate_rainfall_suits_maize: "ఇది సులభమైన పంట, మధ్యస్థ వర్షంలో బాగా పండుతుంది"
      },
      soils: {
        alluvial: "ఒండ్రు (నది) నేల", black: "నల్ల నేల", loamy: "గరప నేల (Loamy)",
        red: "ఎర్ర నేల", sandy: "ఇసుక నేల"
      },
      zones: { delta: "డెల్టా ప్రాంతం", coastal: "తీర ప్రాంతం", upland: "ఎత్తైన ప్రాంతం", semi_arid: "పొడి ప్రాంతం" },
      rainfall: { low: "తక్కువ వర్షం", medium: "మధ్యస్థ వర్షం", high: "ఎక్కువ వర్షం" },
      groundwater: { shallow: "తక్కువ లోతు", medium: "మధ్యస్థం", deep: "ఎక్కువ లోతు" },
      tiers: { watch: "గమనించండి", warning: "హెచ్చరిక", alert: "ప్రమాదం" },

      runDemo: "డెమో చూడండి",
      demoLoading: "డెమో మొదలవుతోంది…",
      demoError: "డెమోకి డేటాబేస్ అవసరం. దయచేసి మళ్ళీ ప్రయత్నించండి.",
      demoNext: "తర్వాత",
      demoExit: "డెమో మూసివేయి",
      demoReplay: "మళ్ళీ చూడండి",
      demoStepOf: function (n, total) { return "అడుగు " + n + " / " + total; },
      demoStep1: "గుంటూరు దగ్గర టమాటా రైతు రమేష్‌ను కలవండి. KisanMate అతని నేల, నీరు, వర్షపాతం చూసి మంచి పంటలను సూచిస్తుంది — టమాటా అతని భూమికి బాగా సరిపోతుంది.",
      demoStep2: "రమేష్ ఒక వ్యాధిగ్రస్త ఆకు ఫోటో తీస్తాడు. కెమెరా త్వరిత తెగులు వైపు మొగ్గు చూపుతుంది, కానీ చల్లని తడి వాతావరణం ఆలస్య తెగులును సూచిస్తుంది — ఇది అంటువ్యాధి. విభేదం ఉన్నందున KisanMate ఊహించదు: దీన్ని నిపుణుడికి పంపుతుంది.",
      demoStep3: "ఒక RSK అధికారి ఫోటో చూసి ఆలస్య తెగులును నిర్ధారిస్తారు. అధికారి తీర్పే అంతిమం — ఇది AIని అధిగమిస్తుంది.",
      demoStep4: "నిర్ధారించిన అంటువ్యాధి సమాజ హెచ్చరికను ప్రేరేపిస్తుంది. 3 కి.మీ. దూరంలోని టమాటా రైతు లక్ష్మికి ఆమె భాష తెలుగులో హెచ్చరిక అందుతుంది. పక్కనే ఉన్న వెంకట్ వరి పండిస్తాడు కాబట్టి అతనికి ఇబ్బంది ఉండదు."
    }
  };

  // Keyword lists for the Home mic's simple command router (no NLU backend
  // exists, so we match a spoken transcript against short keyword lists).
  var VOICE_COMMANDS = {
    en: {
      diagnose: ["diagnose", "crop check", "check my crop", "check crop", "photo", "camera", "sick", "disease"],
      recommend: ["grow", "recommend", "what to grow", "suggest", "plant", "suggestion"],
      alerts: ["alert", "alerts", "warning", "notification"]
    },
    hi: {
      diagnose: ["जांच", "फसल जांच", "फोटो", "बीमारी", "पौधा"],
      recommend: ["उगाएं", "क्या उगाएं", "सुझाव", "फसल सुझाव"],
      alerts: ["सूचना", "चेतावनी", "अलर्ट"]
    },
    te: {
      diagnose: ["పరీక్ష", "పంట పరీక్ష", "ఫోటో", "వ్యాధి"],
      recommend: ["పండించాలి", "ఏమి పండించాలి", "సలహా", "సూచన"],
      alerts: ["హెచ్చరిక", "సూచనలు", "అలర్ట్"]
    }
  };

  global.KM_STRINGS = STRINGS;
  global.KM_LOCALE_TAGS = LOCALE_TAGS;
  global.KM_LANGUAGES = LANGUAGES;
  global.KM_CHOOSE_LANGUAGE_HEADING = CHOOSE_LANGUAGE_HEADING;
  global.KM_VOICE_COMMANDS = VOICE_COMMANDS;
})(window);
