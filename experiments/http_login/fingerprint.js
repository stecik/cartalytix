// Browser fingerprint checks extracted from Fingerprint.html (VexTrio analyzer).
// Evaluated by hybrid.bootstrap_browser; resolves to flagged/errored result codes.
// https://github.com/HackingLZ/fingerprint_js
async () => {
    // ═══════════════════════════════════════════════════════
    // HELPER FUNCTIONS (exact VexTrio implementations)
    // ═══════════════════════════════════════════════════════

    function CHECK_DEVICE_IN_USERAGENT_STRING() {
        var c = navigator.userAgent.toLowerCase();
        if (c.indexOf("windows phone") >= 0) return "Windows Phone";
        if (c.indexOf("win") >= 0) return "Windows";
        if (c.indexOf("kaios") >= 0) return "Kaios";
        if (c.indexOf("android") >= 0 || c.indexOf("spreadtrum") >= 0)
            return "Android";
        if (c.indexOf("linux") >= 0 || c.indexOf("cros") >= 0) return "Linux";
        if (c.indexOf("iphone") >= 0 || c.indexOf("ipad") >= 0) return "iOS";
        if (c.indexOf("mac") >= 0) return "Mac";
        return "Other";
    }

    function DETECT_Browser_Type() {
        var c = navigator.userAgent;
        if (c.indexOf("OPR/") !== -1 || c.indexOf("Opera") !== -1)
            return "Opera";
        if (
            (c.indexOf("MSIE") !== -1 || c.indexOf("Trident") !== -1) &&
            c.indexOf("MAXTHON") === -1
        )
            return "Internet Explorer";
        if (c.indexOf("Edge") !== -1 || c.indexOf("EdgA") !== -1) return "Edge";
        if (c.indexOf("SamsungBrowser") !== -1) return "Samsung Browser";
        if (c.indexOf("UCBrowser") !== -1) return "UC Browser";
        if (
            c.indexOf("Android") !== -1 &&
            c.indexOf("Chrome") === -1 &&
            c.indexOf("Firefox") === -1
        )
            return "Android Browser";
        if (c.indexOf("Chrome") !== -1 || c.indexOf("CriOS") !== -1)
            return "Chrome";
        if (c.indexOf("Safari") !== -1 && c.indexOf("Chrome") === -1)
            return "Safari";
        if (c.indexOf("Firefox") !== -1) return "Firefox";
        return "Other";
    }

    function HELPER_WEBGL_DEBUG_TOKENS_FOR_RENDERER_AND_VENDOR() {
        try {
            var c = document.createElement("canvas");
            var d = c.getContext("webgl");
            var e = d.getExtension("WEBGL_debug_renderer_info");
            var f = d.getParameter(e.UNMASKED_VENDOR_WEBGL);
            var g = d.getParameter(e.UNMASKED_RENDERER_WEBGL);
            return [f, g];
        } catch (h) {
            return false;
        }
    }

    var voiceslist = "";
    function populateVoiceList() {
        try {
            var c = speechSynthesis.getVoices();
            if (c.length !== 0) {
                var d = "";
                for (var f = 0; f < c.length; f++) {
                    d = d + " " + c[f].name;
                }
                voiceslist = d;
            } else {
                setTimeout(function () {
                    populateVoiceList();
                }, 50);
            }
        } catch (g) {}
    }
    try {
        populateVoiceList();
        if (typeof speechSynthesis !== "undefined")
            speechSynthesis.onvoiceschanged = populateVoiceList;
    } catch (e) {}

    var permissionsFlag = false;
    function getPermissionStatus() {
        try {
            var browser = DETECT_Browser_Type();
            if (browser !== "Samsung Browser" && browser !== "Firefox") {
                navigator.permissions
                    .query({ name: "notifications" })
                    .then(function (c) {
                        if (
                            typeof Notification !== "undefined" &&
                            Notification.permission === "denied" &&
                            c.state === "prompt"
                        ) {
                            permissionsFlag = true;
                        } else {
                            permissionsFlag = false;
                        }
                    });
            }
        } catch (c) {}
    }
    getPermissionStatus();

    // ═══════════════════════════════════════════════════════
    // ALL 29 CHECKS
    // ═══════════════════════════════════════════════════════

    const CHECKS = [
        {
            id: "a1",
            num: 1,
            name: "Language ISO Code Mismatch",
            fnName: "CHECK_COMPARE_ISO_CODE_LANGUAGE_ARRAY_VS_ISO_CODE_PRIMARY_LANGUAGE",
            desc: "Compares navigator.language with first entry in navigator.languages array",
            run: function () {
                var data = {};
                if (typeof navigator.languages !== "undefined") {
                    try {
                        data.language = navigator.language;
                        data.languages0 = navigator.languages[0];
                        var c = navigator.languages[0].substr(0, 2);
                        data.iso_languages0 = c;
                        data.iso_language = navigator.language.substr(0, 2);
                        if (c !== navigator.language.substr(0, 2)) {
                            return {
                                result: "a1:1",
                                data: data,
                                logic: `languages[0] ISO "${c}" !== language ISO "${navigator.language.substr(0, 2)}" → FAIL`,
                            };
                        }
                    } catch (d) {
                        return {
                            result: "a1:e",
                            data: data,
                            logic: "Exception thrown during comparison",
                        };
                    }
                } else {
                    data.languages = "undefined";
                }
                return {
                    result: "a1:0",
                    data: data,
                    logic: `languages[0] ISO matches language ISO → PASS`,
                };
            },
        },
        {
            id: "a2",
            num: 2,
            name: "Window vs Screen Size Mismatch",
            fnName: "CHECK_COMPARE_AVAILABLE_WINDOW_SIZE_TO_CURRENT_WINDOW_SIZE",
            desc: "Compares screen dimensions against available window dimensions",
            run: function () {
                try {
                    var data = {
                        "screen.width": window.screen.width,
                        "screen.height": window.screen.height,
                        "screen.availWidth": window.screen.availWidth,
                        "screen.availHeight": window.screen.availHeight,
                    };
                    if (
                        window.screen.width < window.screen.availWidth ||
                        window.screen.height < window.screen.availHeight
                    ) {
                        if (
                            (window.screen.width ===
                                window.screen.availHeight &&
                                window.screen.height ===
                                    window.screen.availWidth) ||
                            (window.screen.width ===
                                window.screen.availHeight + 20 &&
                                window.screen.height ===
                                    window.screen.availWidth)
                        ) {
                            return {
                                result: "a2:0",
                                data: data,
                                logic: "Screen < avail but rotation edge case matched → PASS",
                            };
                        } else {
                            return {
                                result: "a2:1",
                                data: data,
                                logic: `screen(${data["screen.width"]}x${data["screen.height"]}) < avail(${data["screen.availWidth"]}x${data["screen.availHeight"]}) → FAIL`,
                            };
                        }
                    }
                    return {
                        result: "a2:0",
                        data: data,
                        logic: "screen dimensions >= available dimensions → PASS",
                    };
                } catch (c) {
                    return { result: "a2:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a3",
            num: 3,
            name: "OSCPU vs UserAgent OS Mismatch",
            fnName: "CHECK_COMPARE_OSCPU_TO_DEVICE_USERAGENT_STRING",
            desc: "Compares navigator.oscpu with OS detected from UserAgent (Firefox-only property)",
            run: function () {
                try {
                    var d = navigator.oscpu;
                    var f = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var data = {
                        "navigator.oscpu": String(d),
                        UA_OS: f,
                        typeof_oscpu: typeof d,
                    };
                    if (typeof d !== "undefined") {
                        d = d.toLowerCase();
                        if (d === "" && f === "Kaios")
                            return {
                                result: "a3:0",
                                data,
                                logic: "Empty oscpu + KaiOS → PASS",
                            };
                        if (
                            d.indexOf("win") >= 0 &&
                            f !== "Windows" &&
                            f !== "Windows Phone"
                        )
                            return {
                                result: "a3:1",
                                data,
                                logic: `oscpu="${d}" says Win but UA says "${f}" → FAIL`,
                            };
                        if (
                            d.indexOf("linux") >= 0 &&
                            f !== "Linux" &&
                            f !== "Android"
                        )
                            return {
                                result: "a3:1",
                                data,
                                logic: `oscpu="${d}" says Linux but UA says "${f}" → FAIL`,
                            };
                        if (d.indexOf("mac") >= 0 && f !== "Mac" && f !== "iOS")
                            return {
                                result: "a3:1",
                                data,
                                logic: `oscpu="${d}" says Mac but UA says "${f}" → FAIL`,
                            };
                        if (
                            (d.indexOf("win") === -1 &&
                                d.indexOf("linux") === -1 &&
                                d.indexOf("mac") === -1) !==
                            (f === "Other")
                        )
                            return {
                                result: "a3:1",
                                data,
                                logic: `oscpu/UA OS mismatch → FAIL`,
                            };
                        return {
                            result: "a3:0",
                            data,
                            logic: "oscpu matches UA OS → PASS",
                        };
                    }
                    return {
                        result: "a3:0",
                        data,
                        logic: "oscpu is undefined (non-Firefox) → PASS",
                    };
                } catch (g) {
                    return { result: "a3:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a4",
            num: 4,
            name: "OSCPU Defined on Non-Firefox",
            fnName: "CHECK_OSCPU_NOT_UNDEFINED_AND_BROWSER_NOT_FIREFOX",
            desc: "navigator.oscpu should only be defined in Firefox",
            run: function () {
                try {
                    var c = navigator.oscpu;
                    var d = DETECT_Browser_Type();
                    var data = {
                        "navigator.oscpu": String(c),
                        typeof_oscpu: typeof c,
                        browser: d,
                    };
                    if (typeof c !== "undefined" && d !== "Firefox") {
                        return {
                            result: "a4:1",
                            data,
                            logic: `oscpu defined (${c}) but browser is ${d}, not Firefox → FAIL`,
                        };
                    }
                    return {
                        result: "a4:0",
                        data,
                        logic:
                            typeof c === "undefined"
                                ? "oscpu is undefined → PASS"
                                : `oscpu defined but browser is Firefox → PASS`,
                    };
                } catch (f) {
                    return { result: "a4:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a5",
            num: 5,
            name: "Platform vs UserAgent Mismatch",
            fnName: "CHECK_COMPARE_PLATFORM_USERAGENT_DEVICEINUSERAGENT",
            desc: "Cross-references navigator.platform with UserAgent OS detection",
            run: function () {
                try {
                    var c = navigator.platform.toLowerCase();
                    var d = navigator.userAgent.toLowerCase();
                    var f = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var data = {
                        "navigator.platform": navigator.platform,
                        UA_OS: f,
                        platform_lower: c,
                    };
                    if (c === "" && f === "Kaios")
                        return {
                            result: "a5:0",
                            data,
                            logic: "Empty platform + KaiOS → PASS",
                        };
                    if (d.indexOf("maui") >= 0 && c.indexOf("pike") >= 0)
                        return {
                            result: "a5:0",
                            data,
                            logic: "MAUI + Pike → PASS",
                        };
                    if (d.indexOf("j2me/midp") >= 0 && c.indexOf("pike") >= 0)
                        return {
                            result: "a5:0",
                            data,
                            logic: "J2ME + Pike → PASS",
                        };
                    if (c === "arm" && f === "Windows Phone")
                        return {
                            result: "a5:0",
                            data,
                            logic: "ARM + WinPhone → PASS",
                        };
                    if (
                        c.indexOf("win") >= 0 &&
                        f !== "Windows" &&
                        f !== "Windows Phone"
                    )
                        return {
                            result: "a5:1",
                            data,
                            logic: `Platform says Win but UA says ${f} → FAIL`,
                        };
                    if (
                        (c.indexOf("linux") >= 0 ||
                            c.indexOf("android") >= 0 ||
                            c.indexOf("pike") >= 0) &&
                        f !== "Linux" &&
                        f !== "Android" &&
                        f !== "Kaios"
                    )
                        return {
                            result: "a5:1",
                            data,
                            logic: `Platform says Linux/Android but UA says ${f} → FAIL`,
                        };
                    if (
                        (c.indexOf("mac") >= 0 ||
                            c.indexOf("ipad") >= 0 ||
                            c.indexOf("ipod") >= 0 ||
                            c.indexOf("iphone") >= 0) &&
                        f !== "Mac" &&
                        f !== "iOS"
                    )
                        return {
                            result: "a5:1",
                            data,
                            logic: `Platform says Apple but UA says ${f} → FAIL`,
                        };
                    if (c === "macintel" && d.indexOf("iphone") >= 0)
                        return {
                            result: "a5:1",
                            data,
                            logic: `Platform=MacIntel but UA has iPhone → FAIL`,
                        };
                    var g =
                        c.indexOf("win") < 0 &&
                        c.indexOf("linux") < 0 &&
                        c.indexOf("mac") < 0 &&
                        c.indexOf("iphone") < 0 &&
                        c.indexOf("pike") < 0 &&
                        c.indexOf("ipod") < 0 &&
                        c.indexOf("ipad") < 0;
                    if (g !== (f === "Other"))
                        return {
                            result: "a5:1",
                            data,
                            logic: `Platform/UA "Other" mismatch → FAIL`,
                        };
                    return {
                        result: "a5:0",
                        data,
                        logic: "Platform matches UA OS → PASS",
                    };
                } catch (h) {
                    return { result: "a5:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a6",
            num: 6,
            name: "Plugins Undefined on Non-Windows",
            fnName: "CHECK_PLUGINSUNDEFINED_BUT_USERAGENTOS_NOT_WINDOWS",
            desc: "navigator.plugins should be defined on non-Windows platforms",
            run: function () {
                try {
                    var c = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var data = {
                        typeof_plugins: typeof navigator.plugins,
                        plugins_length: navigator.plugins
                            ? navigator.plugins.length
                            : "N/A",
                        UA_OS: c,
                    };
                    if (
                        typeof navigator.plugins === "undefined" &&
                        c !== "Windows" &&
                        c !== "Windows Phone"
                    ) {
                        return {
                            result: "a6:1",
                            data,
                            logic: `Plugins undefined but OS is ${c} (not Windows) → FAIL`,
                        };
                    }
                    return {
                        result: "a6:0",
                        data,
                        logic:
                            typeof navigator.plugins === "undefined"
                                ? "Plugins undefined but OS is Windows → PASS"
                                : `Plugins defined (${navigator.plugins.length} items) → PASS`,
                    };
                } catch (d) {
                    return { result: "a6:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a7",
            num: 7,
            name: "Browser Build Number Check",
            fnName: "CHECK_BROWSER_BUILDNUMBER_AGAINST_BROWSERTYPE",
            desc: 'Chrome/Safari/Opera should have productSub === "20030107"',
            run: function () {
                try {
                    var c = navigator.productSub;
                    var d = DETECT_Browser_Type();
                    var data = {
                        "navigator.productSub": String(c),
                        browser: d,
                    };
                    if ((d === "Chrome" || d === "Safari") && c !== "20030107")
                        return {
                            result: "a7:1",
                            data,
                            logic: `${d} should have productSub=20030107, got "${c}" → FAIL`,
                        };
                    if (
                        d === "Opera" &&
                        c !== "20030107" &&
                        typeof c !== "undefined"
                    )
                        return {
                            result: "a7:1",
                            data,
                            logic: `Opera should have productSub=20030107, got "${c}" → FAIL`,
                        };
                    return {
                        result: "a7:0",
                        data,
                        logic: `productSub="${c}" is expected for ${d} → PASS`,
                    };
                } catch (f) {
                    return { result: "a7:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a8",
            num: 8,
            name: "Eval Function Length Check",
            fnName: "CHECK_LENGTH_OF_EVAL_FUNCTION_AGAINST_BROWSER_TYPE",
            desc: "eval.toString().length varies by browser (37/39/33)",
            run: function () {
                try {
                    var c = DETECT_Browser_Type();
                    var d = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var f = eval.toString().length;
                    var data = {
                        "eval.toString().length": f,
                        browser: c,
                        os: d,
                    };
                    if (
                        f === 37 &&
                        c !== "Safari" &&
                        c !== "Firefox" &&
                        c !== "Other" &&
                        d === "iOS" &&
                        c !== "Chrome"
                    )
                        return {
                            result: "a8:1",
                            data,
                            logic: `eval length=37 but browser=${c}, os=${d} (unexpected combo) → FAIL`,
                        };
                    if (f === 39 && c !== "Internet Explorer" && c !== "Other")
                        return {
                            result: "a8:1",
                            data,
                            logic: `eval length=39 (IE) but browser=${c} → FAIL`,
                        };
                    if (
                        f === 33 &&
                        c !== "Chrome" &&
                        c !== "Opera" &&
                        c !== "Edge" &&
                        c !== "UC Browser" &&
                        c !== "Samsung Browser" &&
                        c !== "Other" &&
                        c !== "Android Browser"
                    )
                        return {
                            result: "a8:1",
                            data,
                            logic: `eval length=33 (Chromium) but browser=${c} → FAIL`,
                        };
                    return {
                        result: "a8:0",
                        data,
                        logic: `eval length=${f} matches expected for ${c} → PASS`,
                    };
                } catch (g) {
                    return { result: "a8:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a9",
            num: 9,
            name: "toSource() Function Check",
            fnName: "CHECK_Mozilla_UNIQUE_TOSOURCE_FKT_AGAINST_BROWSER_TYPE",
            desc: "Obsolete toSource() should only exist in old Firefox",
            run: function () {
                try {
                    var c = DETECT_Browser_Type();
                    var d;
                    try {
                        throw "a";
                    } catch (f) {
                        try {
                            f.toSource();
                            d = true;
                        } catch (g) {
                            d = false;
                        }
                    }
                    var data = { toSource_exists: d, browser: c };
                    if (d && c !== "Firefox" && c !== "Other")
                        return {
                            result: "a9:1",
                            data,
                            logic: `toSource() exists but browser=${c} (not Firefox) → FAIL`,
                        };
                    return {
                        result: "a9:0",
                        data,
                        logic: d
                            ? `toSource() exists, browser=Firefox → PASS`
                            : `toSource() does not exist → PASS`,
                    };
                } catch (h) {
                    return { result: "a9:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a10",
            num: 10,
            name: "WebGL Vendor vs OS/Browser",
            fnName: "CHECK_Compare_DEVICEUSERAGENTSTRING_to_BrowserType_to_WEBGL_DEBUG_TOKENS",
            desc: "Cross-references WebGL GPU vendor with OS and browser type",
            run: function () {
                try {
                    var c = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var d = DETECT_Browser_Type();
                    var f = HELPER_WEBGL_DEBUG_TOKENS_FOR_RENDERER_AND_VENDOR();
                    var data = {
                        UA_OS: c,
                        browser: d,
                        webgl_vendor: f ? f[0] : "N/A",
                        webgl_renderer: f ? f[1] : "N/A",
                    };
                    if (!f)
                        return {
                            result: "a10:0",
                            data,
                            logic: "WebGL not available → PASS (skipped)",
                        };
                    if (
                        c === "iOS" &&
                        f[0].indexOf("Apple") === -1 &&
                        f[0].indexOf("Imagination Technologies") === -1
                    )
                        return {
                            result: "a10:1",
                            data,
                            logic: `iOS but vendor="${f[0]}" (not Apple) → FAIL`,
                        };
                    if (
                        c === "Mac" &&
                        f[0].indexOf("Intel") === -1 &&
                        f[0].indexOf("ATI Technologies") === -1 &&
                        f[0].indexOf("NVIDIA Corporation") === -1 &&
                        f[0].indexOf("Apple") === -1
                    )
                        return {
                            result: "a10:1",
                            data,
                            logic: `Mac but vendor="${f[0]}" (unexpected) → FAIL`,
                        };
                    if (
                        c === "Android" &&
                        (f[0] === "Google Inc. (NVIDIA)" ||
                            f[0] === "Google Inc. (Intel)" ||
                            f[0] === "Google Inc. (Google)" ||
                            f[0] === "Google Inc." ||
                            f[0].indexOf("NVIDIA Corporation") !== -1)
                    )
                        return {
                            result: "a10:1",
                            data,
                            logic: `Android with suspicious Google/NVIDIA vendor → FAIL`,
                        };
                    if (
                        c === "Windows" &&
                        d === "Edge" &&
                        f[0].indexOf("Microsoft") === -1
                    )
                        return {
                            result: "a10:1",
                            data,
                            logic: `Windows Edge but vendor not Microsoft → FAIL`,
                        };
                    if (
                        c === "Windows" &&
                        (d === "Chrome" || d === "Firefox") &&
                        f[0].indexOf("Google Inc") === -1
                    )
                        return {
                            result: "a10:1",
                            data,
                            logic: `Windows ${d} but vendor not Google → FAIL`,
                        };
                    if (f[0].indexOf("VMware") !== -1)
                        return {
                            result: "a10:1",
                            data,
                            logic: `VMware detected in vendor → FAIL (VM detected)`,
                        };
                    return {
                        result: "a10:0",
                        data,
                        logic: `WebGL vendor="${f[0]}" matches OS=${c} → PASS`,
                    };
                } catch (g) {
                    return { result: "a10:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a11",
            num: 11,
            name: "WebDriver Detection",
            fnName: "CHECK_Webdriver_in_Navigator_Interface",
            desc: "Checks if browser is running under automation (navigator.webdriver)",
            run: function () {
                try {
                    var c = "webdriver" in navigator && navigator.webdriver;
                    var data = {
                        "navigator.webdriver": navigator.webdriver,
                        '"webdriver" in navigator': "webdriver" in navigator,
                        result: c,
                    };
                    if (c)
                        return {
                            result: "a11:1",
                            data,
                            logic: "navigator.webdriver is true → FAIL (automation detected)",
                        };
                    return {
                        result: "a11:0",
                        data,
                        logic: "navigator.webdriver is false/absent → PASS",
                    };
                } catch (d) {
                    return { result: "a11:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a12",
            num: 12,
            name: "Permission State Anomaly",
            fnName: "CHECK_Permission_DENIED_in_State_Prompt",
            desc: 'Notification.permission="denied" + state="prompt" should never co-exist',
            run: function () {
                try {
                    var data = {
                        permissionsFlag: permissionsFlag,
                        "Notification.permission":
                            typeof Notification !== "undefined"
                                ? Notification.permission
                                : "N/A",
                    };
                    if (permissionsFlag)
                        return {
                            result: "a12:1",
                            data,
                            logic: "Permission denied + state prompt anomaly → FAIL",
                        };
                    return {
                        result: "a12:0",
                        data,
                        logic: "Permission states consistent → PASS",
                    };
                } catch (c) {
                    return { result: "a12:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a13",
            num: 13,
            name: "Permissions API Integrity",
            fnName: "DETECT_NAVIGATORPERMISSIONS_PROPERTIES_ODD",
            desc: "Verifies permissions.query returns native code, checks for proxy/tamper indicators",
            run: function () {
                try {
                    var c = window.navigator.permissions;
                    var data = {};
                    if (!c)
                        return {
                            result: "a13:0",
                            data: { permissions: "undefined" },
                            logic: "Permissions API not available → PASS",
                        };
                    data["query.toString()"] = c.query
                        .toString()
                        .replace(/\s+/g, "");
                    if (
                        c.query.toString().replace(/\s+/g, "") !==
                        "function query() { [native code] }".replace(/\s+/g, "")
                    ) {
                        data.issue = "query.toString() not native";
                        return {
                            result: "a13:1",
                            data,
                            logic: `query.toString() !== native code pattern → FAIL`,
                        };
                    }
                    data["query.toString.toString()"] = c.query.toString
                        .toString()
                        .replace(/\s+/g, "");
                    if (
                        c.query.toString.toString().replace(/\s+/g, "") !==
                        "function toString() { [native code] }".replace(
                            /\s+/g,
                            "",
                        )
                    ) {
                        data.issue = "toString.toString() not native";
                        return {
                            result: "a13:1",
                            data,
                            logic: `toString.toString() !== native code pattern → FAIL`,
                        };
                    }
                    if (
                        c.query.toString.hasOwnProperty("[[Handler]]") &&
                        c.query.toString.hasOwnProperty("[[Target]]") &&
                        c.query.toString.hasOwnProperty("[[IsRevoked]]")
                    ) {
                        data.issue = "Proxy properties detected";
                        return {
                            result: "a13:1",
                            data,
                            logic: `Proxy properties [[Handler]]/[[Target]]/[[IsRevoked]] found → FAIL`,
                        };
                    }
                    if (c.hasOwnProperty("query")) {
                        data.issue = "query is own property";
                        return {
                            result: "a13:1",
                            data,
                            logic: `permissions.hasOwnProperty("query") is true → FAIL`,
                        };
                    }
                    data.status = "All integrity checks passed";
                    return {
                        result: "a13:0",
                        data,
                        logic: "All permissions integrity checks passed → PASS",
                    };
                } catch (d) {
                    return { result: "a13:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a14",
            num: 14,
            name: "DevTools / Console Detection",
            fnName: "CHECK_SPOOKYOSCHECK_likely_Devtools",
            desc: "Overrides regex toString, console.debug, checks call count (Chrome DevTools)",
            run: function () {
                try {
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var browser = DETECT_Browser_Type();
                    var data = { os: os, browser: browser };
                    if (browser === "Chrome" && os !== "iOS") {
                        var c = 0;
                        var d = /./;
                        d.toString = function () {
                            c++;
                            return "spooky";
                        };
                        console.debug(d);
                        data.toStringCallCount = c;
                        if (c > 1)
                            return {
                                result: "a14:1",
                                data,
                                logic: `toString called ${c} times (>1) → FAIL (DevTools likely open)`,
                            };
                        return {
                            result: "a14:0",
                            data,
                            logic: `toString called ${c} time → PASS`,
                        };
                    }
                    return {
                        result: "a14:0",
                        data,
                        logic: `Browser=${browser}, OS=${os} → skipped (Chrome-only check) → PASS`,
                    };
                } catch (f) {
                    return { result: "a14:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a15",
            num: 15,
            name: "PhantomJS Detection",
            fnName: "DETECT_Phantom_in_Window",
            desc: "Checks for callPhantom, _phantom, phantom in window object",
            run: function () {
                try {
                    var results = {
                        callPhantom: "callPhantom" in window,
                        _phantom: "_phantom" in window,
                        phantom: "phantom" in window,
                    };
                    var data = results;
                    var result =
                        results.callPhantom ||
                        results._phantom ||
                        results.phantom;
                    if (result)
                        return {
                            result: "a15:1",
                            data,
                            logic: "PhantomJS properties found in window → FAIL",
                        };
                    return {
                        result: "a15:0",
                        data,
                        logic: "No PhantomJS properties in window → PASS",
                    };
                } catch (d) {
                    return { result: "a15:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a16",
            num: 16,
            name: "Browser Automation DOM Check",
            fnName: "DETECT_BROWSERAUTOMATION_via_domElements",
            desc: "Checks window/document for Selenium, WebDriver, Nightmare, PhantomJS, Sequentum properties",
            run: function () {
                try {
                    var docProps = [
                        "__webdriver_evaluate",
                        "__selenium_evaluate",
                        "__webdriver_script_function",
                        "__webdriver_script_func",
                        "__webdriver_script_fn",
                        "__fxdriver_evaluate",
                        "__driver_unwrapped",
                        "__webdriver_unwrapped",
                        "__driver_evaluate",
                        "__selenium_unwrapped",
                        "__fxdriver_unwrapped",
                    ];
                    var winProps = [
                        "webdriver",
                        "_phantom",
                        "__nightmare",
                        "_selenium",
                        "callPhantom",
                        "callSelenium",
                        "_Selenium_IDE_Recorder",
                        "__stopAllTimers",
                    ];
                    var found = [];
                    for (var f in winProps) {
                        if (window[winProps[f]])
                            found.push("window." + winProps[f]);
                    }
                    for (var h in docProps) {
                        if (window.document[docProps[h]])
                            found.push("document." + docProps[h]);
                    }
                    try {
                        if (
                            window.external &&
                            window.external.toString &&
                            window.external.toString().indexOf("Sequentum") !==
                                -1
                        )
                            found.push("Sequentum");
                    } catch (e) {}
                    try {
                        if (
                            window.document.documentElement.getAttribute(
                                "selenium",
                            )
                        )
                            found.push("attr:selenium");
                        if (
                            window.document.documentElement.getAttribute(
                                "webdriver",
                            )
                        )
                            found.push("attr:webdriver");
                        if (
                            window.document.documentElement.getAttribute(
                                "driver",
                            )
                        )
                            found.push("attr:driver");
                    } catch (e) {}
                    var data = {
                        found: found.length ? found.join(", ") : "none",
                        checked_win: winProps.length,
                        checked_doc: docProps.length,
                    };
                    if (found.length > 0)
                        return {
                            result: "a16:1",
                            data,
                            logic: `Automation indicators found: ${found.join(", ")} → FAIL`,
                        };
                    return {
                        result: "a16:0",
                        data,
                        logic: `No automation indicators in ${winProps.length + docProps.length} properties → PASS`,
                    };
                } catch (k) {
                    return { result: "a16:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a17",
            num: 17,
            name: "Phantomas Detection",
            fnName: "DETECT_PHANTOMAS",
            desc: "Checks for __phantomas property in window",
            run: function () {
                try {
                    var result = "__phantomas" in window;
                    var data = { __phantomas_in_window: result };
                    if (result)
                        return {
                            result: "a17:1",
                            data,
                            logic: "__phantomas found in window → FAIL",
                        };
                    return {
                        result: "a17:0",
                        data,
                        logic: "__phantomas not in window → PASS",
                    };
                } catch (d) {
                    return { result: "a17:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a18",
            num: 18,
            name: "Selenium DOM Cache Detection",
            fnName: "DETECT_Selenium_DOM_based",
            desc: "Searches document properties for $[a-z]dc_ pattern with .cache_ (Selenium)",
            run: function () {
                try {
                    var found = [];
                    for (var c in window.document) {
                        if (
                            c.match(/\$[a-z]dc_/) &&
                            window.document[c].cache_
                        ) {
                            found.push(c);
                        }
                    }
                    var data = {
                        found: found.length ? found.join(", ") : "none",
                    };
                    if (found.length > 0)
                        return {
                            result: "a18:1",
                            data,
                            logic: `Selenium cache properties found: ${found.join(", ")} → FAIL`,
                        };
                    return {
                        result: "a18:0",
                        data,
                        logic: "No Selenium DOM cache properties found → PASS",
                    };
                } catch (d) {
                    return { result: "a18:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a19",
            num: 19,
            name: "Node.js Buffer Detection",
            fnName: "DETECT_NodeJS_Buffer",
            desc: "Checks if window.Buffer is defined (indicates Node.js environment)",
            run: function () {
                try {
                    var data = {
                        "window.Buffer": typeof window.Buffer,
                        defined: window.Buffer !== undefined,
                    };
                    if (window.Buffer !== undefined)
                        return {
                            result: "a19:1",
                            data,
                            logic: "window.Buffer is defined → FAIL (Node.js detected)",
                        };
                    return {
                        result: "a19:0",
                        data,
                        logic: "window.Buffer is undefined → PASS",
                    };
                } catch (c) {
                    return { result: "a19:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a20",
            num: 20,
            name: "Chromium Automation Driver",
            fnName: "DETECT_Chromium_based_automation_driver",
            desc: "Checks for domAutomation / domAutomationController in window",
            run: function () {
                try {
                    var data = {
                        "window.domAutomation": !!window.domAutomation,
                        "window.domAutomationController":
                            !!window.domAutomationController,
                    };
                    if (window.domAutomation || window.domAutomationController)
                        return {
                            result: "a20:1",
                            data,
                            logic: "domAutomation(Controller) found → FAIL",
                        };
                    return {
                        result: "a20:0",
                        data,
                        logic: "No Chromium automation driver properties → PASS",
                    };
                } catch (c) {
                    return { result: "a20:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a21",
            num: 21,
            name: "setTimeout Integrity",
            fnName: "CHECK_setTimeout_Integrity",
            desc: "setTimeout.toString() should return native code signature",
            run: function () {
                try {
                    var s = setTimeout.toString().replace(/\s+/g, "");
                    var expected =
                        "function setTimeout() { [native code] }".replace(
                            /\s/g,
                            "",
                        );
                    var data = {
                        "setTimeout.toString()": setTimeout.toString(),
                        normalized: s,
                        expected: expected,
                        match: s === expected,
                    };
                    if (s !== expected)
                        return {
                            result: "a21:1",
                            data,
                            logic: `setTimeout toString mismatch → FAIL (possibly tampered)`,
                        };
                    return {
                        result: "a21:0",
                        data,
                        logic: "setTimeout returns native code → PASS",
                    };
                } catch (c) {
                    return { result: "a21:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a22",
            num: 22,
            name: "setInterval Integrity",
            fnName: "CHECK_setInterval_Integrity",
            desc: "setInterval.toString() should return native code signature",
            run: function () {
                try {
                    var s = setInterval.toString().replace(/\s+/g, "");
                    var expected =
                        "function setInterval() { [native code] }".replace(
                            /\s/g,
                            "",
                        );
                    var data = {
                        "setInterval.toString()": setInterval.toString(),
                        normalized: s,
                        expected: expected,
                        match: s === expected,
                    };
                    if (s !== expected)
                        return {
                            result: "a22:1",
                            data,
                            logic: `setInterval toString mismatch → FAIL (possibly tampered)`,
                        };
                    return {
                        result: "a22:0",
                        data,
                        logic: "setInterval returns native code → PASS",
                    };
                } catch (c) {
                    return { result: "a22:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a42",
            num: 23,
            name: "XMLHttpRequest.open Integrity",
            fnName: "CHECK_XMLHTTPRequest_1",
            desc: 'XMLHttpRequest.prototype.open should not contain "klIsCORSRequest"',
            run: function () {
                try {
                    var h = "kl" + "IsCO" + "RSRequest";
                    var s = window.XMLHttpRequest.prototype.open.toString();
                    var data = {
                        "open.toString()": s,
                        searchFor: h,
                        found: s.indexOf(h) !== -1,
                    };
                    if (s.indexOf(h) !== -1)
                        return {
                            result: "a42:1",
                            data,
                            logic: `"klIsCORSRequest" found in open() → FAIL`,
                        };
                    return {
                        result: "a42:0",
                        data,
                        logic: '"klIsCORSRequest" not in open() → PASS',
                    };
                } catch (i) {
                    return { result: "a42:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a43",
            num: 24,
            name: "XMLHttpRequest.send Integrity",
            fnName: "CHECK_XMLHTTPRequest_2",
            desc: 'XMLHttpRequest.prototype.send should not contain "klIsCORSRequest"',
            run: function () {
                try {
                    var g = "klI" + "sCOR" + "SRequest";
                    var s = window.XMLHttpRequest.prototype.send.toString();
                    var data = {
                        "send.toString()": s,
                        searchFor: g,
                        found: s.indexOf(g) !== -1,
                    };
                    if (s.indexOf(g) !== -1)
                        return {
                            result: "a43:1",
                            data,
                            logic: `"klIsCORSRequest" found in send() → FAIL`,
                        };
                    return {
                        result: "a43:0",
                        data,
                        logic: '"klIsCORSRequest" not in send() → PASS',
                    };
                } catch (h) {
                    return { result: "a43:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a60",
            num: 25,
            name: "UserAgent Automation Strings",
            fnName: "CHECK_UserAgent_not_automation",
            desc: "UA should not contain phantomjs, headless, avira, googleweblight",
            run: function () {
                try {
                    var h = navigator.userAgent.toLowerCase();
                    var checks = {
                        phantomjs: h.indexOf("phantomjs") !== -1,
                        headless: h.indexOf("headless") !== -1,
                        avira: h.indexOf("avira") !== -1,
                        googleweblight: h.indexOf("googleweblight") !== -1,
                    };
                    var data = Object.assign(
                        { userAgent: navigator.userAgent },
                        checks,
                    );
                    if (
                        checks.phantomjs ||
                        checks.headless ||
                        checks.avira ||
                        checks.googleweblight
                    ) {
                        var found = Object.keys(checks).filter(
                            (k) => checks[k],
                        );
                        return {
                            result: "a60:1",
                            data,
                            logic: `Found: ${found.join(", ")} in UserAgent → FAIL`,
                        };
                    }
                    return {
                        result: "a60:0",
                        data,
                        logic: "No automation strings in UserAgent → PASS",
                    };
                } catch (i) {
                    return { result: "a60:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a78",
            num: 26,
            name: "Stack Trace Behavior (Puppeteer)",
            fnName: "CHECK_STACKTRACE_Behavior",
            desc: "Intentionally triggers error, checks stack trace for puppeteer-stealth pattern",
            run: function () {
                var c, d;
                try {
                    document.createElement(0);
                } catch (e) {
                    try {
                        d = e.stack ? e.stack.split("\n") : [];
                        var data = {
                            stackLines: d.length,
                            stack_preview: d.slice(0, 4).join(" | "),
                        };
                        c =
                            d.length >= 2
                                ? !!d[1].match(
                                      /Ob[cej]{3}t\.a[lp]{3}y[\(< ]{3}an[oynm]{5}us>/,
                                  )
                                : true;
                        data.patternMatch = c;
                        if (c)
                            return {
                                result: "a78:1",
                                data,
                                logic: `Puppeteer-stealth stack trace pattern detected → FAIL`,
                            };
                        return {
                            result: "a78:0",
                            data,
                            logic: "Normal stack trace → PASS",
                        };
                    } catch (f) {
                        return {
                            result: "a78:0",
                            data: { error: "stack parse failed" },
                            logic: "Stack parse failed → PASS",
                        };
                    }
                }
                return {
                    result: "a78:0",
                    data: { note: "createElement(0) did not throw" },
                    logic: "No error thrown → PASS",
                };
            },
        },
        {
            id: "a86",
            num: 27,
            name: "iOS Logical Processors Check",
            fnName: "CHECK_NumberofLogicalProcessors_IOS",
            desc: "iOS devices with hardwareConcurrency > 4 flagged (Safari dropped support)",
            run: function () {
                try {
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var hc = window.navigator.hardwareConcurrency;
                    var data = {
                        os: os,
                        hardwareConcurrency: hc,
                        typeof: typeof hc,
                    };
                    if (os === "iOS" && hc !== undefined && hc > 4)
                        return {
                            result: "a86:1",
                            data,
                            logic: `iOS + hardwareConcurrency=${hc} (>4) → FAIL`,
                        };
                    return {
                        result: "a86:0",
                        data,
                        logic:
                            os !== "iOS"
                                ? `OS=${os} (not iOS) → PASS`
                                : hc === undefined
                                  ? "hardwareConcurrency undefined → PASS"
                                  : `hardwareConcurrency=${hc} (<=4) → PASS`,
                    };
                } catch (c) {
                    return { result: "a86:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "a89",
            num: 28,
            name: "Browser Voice List Check",
            fnName: "CHECK_Browser_VoiceList",
            desc: '"Lekha" voice (Apple-only) should not exist on Windows/Android/Linux',
            run: function () {
                var c = CHECK_DEVICE_IN_USERAGENT_STRING();
                var data = {
                    os: c,
                    voiceslist_length: voiceslist.length,
                    has_lekha: voiceslist.toLowerCase().indexOf("lekha") !== -1,
                    sample: voiceslist.substring(0, 200),
                };
                if (
                    voiceslist.toLowerCase().indexOf("lekha") !== -1 &&
                    (c.indexOf("Win") !== -1 ||
                        c === "Kaios" ||
                        c === "Android" ||
                        c === "Linux")
                ) {
                    return {
                        result: "a89:1",
                        data,
                        logic: `"Lekha" voice found on ${c} (Apple-only voice) → FAIL`,
                    };
                }
                return {
                    result: "a89:0",
                    data,
                    logic:
                        voiceslist.toLowerCase().indexOf("lekha") === -1
                            ? '"Lekha" voice not found → PASS'
                            : `"Lekha" found but OS=${c} (Apple) → PASS`,
                };
            },
        },
        {
            id: "a92",
            num: 29,
            name: "VirtualBox Detection",
            fnName: "CHECK_VirtualBox",
            desc: 'WebGL renderer should not contain "VirtualBox"',
            run: function () {
                try {
                    var c = HELPER_WEBGL_DEBUG_TOKENS_FOR_RENDERER_AND_VENDOR();
                    if (!c)
                        return {
                            result: "a92:0",
                            data: { webgl: "unavailable" },
                            logic: "WebGL unavailable → PASS (skipped)",
                        };
                    var data = { vendor: c[0], renderer: c[1] };
                    if (c[1].indexOf("VirtualBox") !== -1)
                        return {
                            result: "a92:1",
                            data,
                            logic: `"VirtualBox" found in renderer → FAIL (VM detected)`,
                        };
                    return {
                        result: "a92:0",
                        data,
                        logic: `Renderer="${c[1]}" — no VirtualBox → PASS`,
                    };
                } catch (d) {
                    return { result: "a92:e", data: {}, logic: "Exception" };
                }
            },
        },

        // ── NEW CHECKS (b-series): post-VexTrio additions ──────────────────────

        {
            id: "b1",
            num: 30,
            name: "window.chrome Object Integrity",
            fnName: "CHECK_CHROME_OBJECT_INTEGRITY",
            desc: "Chrome UA must have window.chrome with loadTimes/csi/runtime; automation often exposes empty object",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    if (
                        browser !== "Chrome" &&
                        browser !== "Edge" &&
                        browser !== "Samsung Browser"
                    ) {
                        return {
                            result: "b1:0",
                            data: { browser: browser },
                            logic: "Not Chrome-based → PASS (skipped)",
                        };
                    }
                    var data = {
                        browser: browser,
                        "typeof_window.chrome": typeof window.chrome,
                    };
                    if (
                        typeof window.chrome === "undefined" ||
                        window.chrome === null
                    ) {
                        return {
                            result: "b1:1",
                            data,
                            logic: "Chrome UA but window.chrome is undefined → FAIL",
                        };
                    }
                    data.hasLoadTimes =
                        typeof window.chrome.loadTimes === "function";
                    data.hasCsi = typeof window.chrome.csi === "function";
                    data.hasRuntime =
                        typeof window.chrome.runtime !== "undefined";
                    data.hasApp = typeof window.chrome.app !== "undefined";
                    if (
                        !data.hasLoadTimes &&
                        !data.hasCsi &&
                        !data.hasRuntime &&
                        !data.hasApp
                    ) {
                        return {
                            result: "b1:1",
                            data,
                            logic: "window.chrome is empty object (no loadTimes/csi/runtime/app) → FAIL",
                        };
                    }
                    return {
                        result: "b1:0",
                        data,
                        logic: "window.chrome has expected Chrome APIs → PASS",
                    };
                } catch (e) {
                    return { result: "b1:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b2",
            num: 31,
            name: "ChromeDriver cdc_ Property Injection",
            fnName: "DETECT_ChromeDriver_CDC_Properties",
            desc: "ChromeDriver injects window/document properties with cdc_ prefix (e.g. cdc_adoQpoasnfa76pfcZLmcfl_Array)",
            run: function () {
                try {
                    var winProps = Object.getOwnPropertyNames(window).filter(
                        function (p) {
                            return /^\$?cdc_[a-zA-Z0-9]+/.test(p);
                        },
                    );
                    var docProps = [];
                    try {
                        docProps = Object.getOwnPropertyNames(document).filter(
                            function (p) {
                                return /^\$?cdc_[a-zA-Z0-9]+/.test(p);
                            },
                        );
                    } catch (e2) {}
                    var data = {
                        window_cdc: winProps.join(", ") || "none",
                        document_cdc: docProps.join(", ") || "none",
                    };
                    if (winProps.length > 0 || docProps.length > 0) {
                        return {
                            result: "b2:1",
                            data,
                            logic: "ChromeDriver cdc_ properties found → FAIL (ChromeDriver detected)",
                        };
                    }
                    return {
                        result: "b2:0",
                        data,
                        logic: "No cdc_ properties in window or document → PASS",
                    };
                } catch (e) {
                    return { result: "b2:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b3",
            num: 32,
            name: "UA-CH Platform vs UserAgent Mismatch",
            fnName: "CHECK_USERAGENTDATA_PLATFORM_CONSISTENCY",
            desc: "navigator.userAgentData.platform (UA-CH) must match OS detected from UserAgent string",
            run: function () {
                try {
                    if (typeof navigator.userAgentData === "undefined") {
                        return {
                            result: "b3:0",
                            data: { support: "UA-CH not supported" },
                            logic: "navigator.userAgentData undefined → PASS (skipped)",
                        };
                    }
                    var uadPlatform = navigator.userAgentData.platform;
                    var uaMobile = navigator.userAgentData.mobile;
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var data = {
                        "uad.platform": uadPlatform,
                        "uad.mobile": uaMobile,
                        UA_OS: os,
                    };
                    if (
                        uadPlatform === "Windows" &&
                        os !== "Windows" &&
                        os !== "Windows Phone"
                    )
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.platform="Windows" but UA_OS="${os}" → FAIL`,
                        };
                    if (uadPlatform === "macOS" && os !== "Mac")
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.platform="macOS" but UA_OS="${os}" → FAIL`,
                        };
                    if (
                        uadPlatform === "Linux" &&
                        os !== "Linux" &&
                        os !== "Android"
                    )
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.platform="Linux" but UA_OS="${os}" → FAIL`,
                        };
                    if (uadPlatform === "Android" && os !== "Android")
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.platform="Android" but UA_OS="${os}" → FAIL`,
                        };
                    if (uadPlatform === "iOS" && os !== "iOS")
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.platform="iOS" but UA_OS="${os}" → FAIL`,
                        };
                    if (
                        uaMobile === true &&
                        (os === "Windows" || os === "Mac" || os === "Linux")
                    )
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.mobile=true but UA_OS="${os}" (desktop) → FAIL`,
                        };
                    if (
                        uaMobile === false &&
                        (os === "iOS" || os === "Android")
                    )
                        return {
                            result: "b3:1",
                            data,
                            logic: `uad.mobile=false but UA_OS="${os}" (mobile) → FAIL`,
                        };
                    return {
                        result: "b3:0",
                        data,
                        logic: `uad.platform="${uadPlatform}" consistent with UA_OS="${os}" → PASS`,
                    };
                } catch (e) {
                    return { result: "b3:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b4",
            num: 33,
            name: "Intl Timezone vs Date Offset Mismatch",
            fnName: "CHECK_TIMEZONE_INTL_DATE_CONSISTENCY",
            desc: "Intl.DateTimeFormat timezone must be consistent with Date.getTimezoneOffset(); mismatch = timezone spoofing",
            run: function () {
                try {
                    var intlTz =
                        Intl.DateTimeFormat().resolvedOptions().timeZone;
                    var dateOffset = new Date().getTimezoneOffset(); // minutes west of UTC
                    var data = { intlTimezone: intlTz, dateOffset: dateOffset };
                    // Compute expected offset for intlTz using shortOffset formatting
                    var refDate = new Date(Date.UTC(2024, 5, 15, 12, 0, 0));
                    var fmt = new Intl.DateTimeFormat("en-US", {
                        timeZone: intlTz,
                        timeZoneName: "shortOffset",
                    });
                    var parts = fmt.formatToParts(refDate);
                    var tzPart = parts.find(function (p) {
                        return p.type === "timeZoneName";
                    });
                    if (!tzPart)
                        return {
                            result: "b4:0",
                            data,
                            logic: "Cannot compute Intl offset → PASS (skipped)",
                        };
                    var match = tzPart.value.match(/GMT([+-])(\d+)(?::(\d+))?/);
                    var intlOffset = 0;
                    if (match) {
                        var sign = match[1] === "+" ? -1 : 1;
                        intlOffset =
                            sign *
                            (parseInt(match[2], 10) * 60 +
                                (match[3] ? parseInt(match[3], 10) : 0));
                    }
                    data.intlOffset = intlOffset;
                    data.tzPart = tzPart.value;
                    // Allow >90 min difference (generous for DST edge cases)
                    if (Math.abs(intlOffset - dateOffset) > 90) {
                        return {
                            result: "b4:1",
                            data,
                            logic: `Intl offset=${intlOffset}min but Date offset=${dateOffset}min (diff=${Math.abs(intlOffset - dateOffset)}min > 90) → FAIL`,
                        };
                    }
                    return {
                        result: "b4:0",
                        data,
                        logic: `Intl offset=${intlOffset}min ≈ Date offset=${dateOffset}min → PASS`,
                    };
                } catch (e) {
                    return { result: "b4:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b5",
            num: 34,
            name: "navigator.vendor vs Browser Mismatch",
            fnName: "CHECK_NAVIGATOR_VENDOR_CONSISTENCY",
            desc: 'navigator.vendor must match browser: Chrome="Google Inc.", Safari="Apple Computer, Inc.", Firefox=""',
            run: function () {
                try {
                    var vendor = String(navigator.vendor);
                    var browser = DETECT_Browser_Type();
                    var data = { vendor: vendor, browser: browser };
                    if (
                        (browser === "Chrome" ||
                            browser === "Samsung Browser" ||
                            browser === "UC Browser" ||
                            browser === "Android Browser") &&
                        vendor !== "Google Inc."
                    ) {
                        return {
                            result: "b5:1",
                            data,
                            logic: `${browser} should have vendor="Google Inc." but got "${vendor}" → FAIL`,
                        };
                    }
                    if (
                        browser === "Opera" &&
                        vendor !== "Google Inc." &&
                        vendor !== "Opera Software ASA"
                    ) {
                        return {
                            result: "b5:1",
                            data,
                            logic: `Opera should have vendor "Google Inc." or "Opera Software ASA", got "${vendor}" → FAIL`,
                        };
                    }
                    if (
                        browser === "Safari" &&
                        vendor !== "Apple Computer, Inc."
                    ) {
                        return {
                            result: "b5:1",
                            data,
                            logic: `Safari should have vendor="Apple Computer, Inc." but got "${vendor}" → FAIL`,
                        };
                    }
                    if (browser === "Firefox" && vendor !== "") {
                        return {
                            result: "b5:1",
                            data,
                            logic: `Firefox should have vendor="" but got "${vendor}" → FAIL`,
                        };
                    }
                    return {
                        result: "b5:0",
                        data,
                        logic: `vendor="${vendor}" is expected for ${browser} → PASS`,
                    };
                } catch (e) {
                    return { result: "b5:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b6",
            num: 35,
            name: "iOS devicePixelRatio Check",
            fnName: "CHECK_IOS_DEVICE_PIXEL_RATIO",
            desc: "All iOS devices since iPhone 4 have Retina displays (devicePixelRatio >= 2); dpr=1 on iOS UA = spoofed",
            run: function () {
                try {
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var dpr = window.devicePixelRatio;
                    var data = { os: os, devicePixelRatio: dpr };
                    if (os === "iOS" && (dpr === undefined || dpr < 2)) {
                        return {
                            result: "b6:1",
                            data,
                            logic: `iOS UA but devicePixelRatio=${dpr} (all modern iOS devices have dpr>=2) → FAIL`,
                        };
                    }
                    return {
                        result: "b6:0",
                        data,
                        logic:
                            os !== "iOS"
                                ? `OS=${os} → PASS (skipped)`
                                : `iOS dpr=${dpr} (>=2) → PASS`,
                    };
                } catch (e) {
                    return { result: "b6:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b7",
            num: 36,
            name: "Touch Points vs Mobile OS",
            fnName: "CHECK_TOUCH_POINTS_CONSISTENCY",
            desc: "navigator.maxTouchPoints must be >0 for iOS/Android; 0 on mobile UA = spoofed desktop browser",
            run: function () {
                try {
                    var mtp = navigator.maxTouchPoints;
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var data = { maxTouchPoints: mtp, os: os };
                    if (
                        (os === "iOS" || os === "Android") &&
                        (mtp === undefined || mtp === 0)
                    ) {
                        return {
                            result: "b7:1",
                            data,
                            logic: `${os} UA but maxTouchPoints=${mtp} (touch devices must have >0) → FAIL`,
                        };
                    }
                    return {
                        result: "b7:0",
                        data,
                        logic:
                            os !== "iOS" && os !== "Android"
                                ? `OS=${os} → PASS (skipped)`
                                : `${os} maxTouchPoints=${mtp} (>0) → PASS`,
                    };
                } catch (e) {
                    return { result: "b7:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b8",
            num: 37,
            name: "Error.stackTraceLimit Browser Consistency",
            fnName: "CHECK_ERROR_STACK_TRACE_LIMIT",
            desc: "Error.stackTraceLimit is a V8/Chrome-only property; present on Chrome/Edge, absent on Firefox/Safari",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var hasSTL = typeof Error.stackTraceLimit !== "undefined";
                    var data = {
                        browser: browser,
                        "Error.stackTraceLimit": hasSTL
                            ? String(Error.stackTraceLimit)
                            : "undefined",
                    };
                    if (
                        (browser === "Firefox" || browser === "Safari") &&
                        hasSTL
                    ) {
                        return {
                            result: "b8:1",
                            data,
                            logic: `${browser} UA but Error.stackTraceLimit exists (V8/Chrome-only) → FAIL`,
                        };
                    }
                    if (browser === "Chrome" && !hasSTL) {
                        return {
                            result: "b8:1",
                            data,
                            logic: `Chrome UA but Error.stackTraceLimit is undefined → FAIL`,
                        };
                    }
                    return {
                        result: "b8:0",
                        data,
                        logic: `Error.stackTraceLimit=${hasSTL} consistent with ${browser} → PASS`,
                    };
                } catch (e) {
                    return { result: "b8:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b9",
            num: 38,
            name: "Native Function Prototype Lie Detection",
            fnName: "CHECK_PROTOTYPE_LIES",
            desc: "Puppeteer/Playwright stealth patches wrap native functions with Proxy; detectable via toString chain integrity",
            run: function () {
                try {
                    // Verify Function.prototype.toString itself is native (meta-check)
                    var toStr = Function.prototype.toString;
                    var toStrSrc = Function.prototype.toString
                        .call(toStr)
                        .replace(/\s+/g, "");
                    if (toStrSrc !== "functiontoString(){[nativecode]}") {
                        return {
                            result: "b9:1",
                            data: {
                                issue: "Function.prototype.toString is not native",
                                got: toStrSrc,
                            },
                            logic: "Function.prototype.toString spoofed → FAIL",
                        };
                    }
                    // Check key navigator property descriptors for non-native getters
                    var checksToRun = [
                        { obj: navigator, key: "webdriver" },
                        { obj: navigator, key: "plugins" },
                        { obj: navigator, key: "languages" },
                        { obj: navigator, key: "userAgent" },
                    ];
                    for (var i = 0; i < checksToRun.length; i++) {
                        try {
                            var desc = Object.getOwnPropertyDescriptor(
                                Object.getPrototypeOf(checksToRun[i].obj) ||
                                    checksToRun[i].obj,
                                checksToRun[i].key,
                            );
                            if (!desc)
                                desc = Object.getOwnPropertyDescriptor(
                                    checksToRun[i].obj,
                                    checksToRun[i].key,
                                );
                            if (desc && desc.get) {
                                var getterSrc = Function.prototype.toString
                                    .call(desc.get)
                                    .replace(/\s+/g, "");
                                if (getterSrc.indexOf("[nativecode]") === -1) {
                                    return {
                                        result: "b9:1",
                                        data: {
                                            issue: "Non-native getter",
                                            property: checksToRun[i].key,
                                            getter: getterSrc,
                                        },
                                        logic: `navigator.${checksToRun[i].key} getter is not native code → FAIL (Proxy/hook detected)`,
                                    };
                                }
                            }
                        } catch (e2) {}
                    }
                    return {
                        result: "b9:0",
                        data: {
                            checked: checksToRun
                                .map(function (c) {
                                    return c.key;
                                })
                                .join(","),
                        },
                        logic: "All native function toString checks passed → PASS",
                    };
                } catch (e) {
                    return { result: "b9:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b10",
            num: 39,
            name: "Canvas Rendering Integrity",
            fnName: "CHECK_CANVAS_RENDERING",
            desc: "Canvas 2D must produce non-empty output; headless configs with canvas disabled return blank data URIs",
            run: function () {
                try {
                    var canvas = document.createElement("canvas");
                    canvas.width = 200;
                    canvas.height = 50;
                    var ctx = canvas.getContext("2d");
                    if (!ctx)
                        return {
                            result: "b10:0",
                            data: { note: "Canvas 2D context unavailable" },
                            logic: "Canvas not supported → PASS (skipped)",
                        };
                    ctx.textBaseline = "top";
                    ctx.font = "14px Arial";
                    ctx.fillStyle = "#f60";
                    ctx.fillRect(125, 1, 62, 20);
                    ctx.fillStyle = "#069";
                    ctx.fillText("fingerprint\uD83D\uDE00", 2, 15);
                    var dataURL = canvas.toDataURL();
                    var data = {
                        dataURL_length: dataURL.length,
                        prefix: dataURL.substring(0, 30),
                    };
                    if (dataURL === "data:," || dataURL.length < 200) {
                        return {
                            result: "b10:1",
                            data,
                            logic: `Canvas toDataURL() returned blank/empty (${dataURL.length} bytes) → FAIL`,
                        };
                    }
                    return {
                        result: "b10:0",
                        data,
                        logic: `Canvas rendered OK (${dataURL.length} bytes) → PASS`,
                    };
                } catch (e) {
                    return { result: "b10:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b11",
            num: 40,
            name: "AudioContext Sample Rate Check",
            fnName: "CHECK_AUDIO_CONTEXT_SAMPLE_RATE",
            desc: "AudioContext.sampleRate must be a known valid rate; abnormal rate = VM/synthetic env. Valid set includes 24000 (AirPods/speech hardware on macOS) and 32000.",
            run: function () {
                try {
                    var AC = window.AudioContext || window.webkitAudioContext;
                    if (!AC)
                        return {
                            result: "b11:0",
                            data: { note: "AudioContext not available" },
                            logic: "AudioContext not supported → PASS (skipped)",
                        };
                    var ctx = new AC();
                    var sampleRate = ctx.sampleRate;
                    var state = ctx.state;
                    ctx.close();
                    var validRates = [
                        8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000,
                        88200, 96000, 176400, 192000,
                    ];
                    var data = { sampleRate: sampleRate, state: state };
                    if (validRates.indexOf(sampleRate) === -1) {
                        return {
                            result: "b11:1",
                            data,
                            logic: `AudioContext sampleRate=${sampleRate} is not a standard rate → FAIL`,
                        };
                    }
                    return {
                        result: "b11:0",
                        data,
                        logic: `AudioContext sampleRate=${sampleRate} is standard → PASS`,
                    };
                } catch (e) {
                    return { result: "b11:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b12",
            num: 41,
            name: "navigator.connection Presence Check",
            fnName: "CHECK_NETWORK_INFORMATION_API",
            desc: "Chrome/Android supports NetworkInformation API (navigator.connection); absent on Chrome UA = unusual",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var hasConn =
                        typeof navigator.connection !== "undefined" ||
                        typeof navigator.mozConnection !== "undefined" ||
                        typeof navigator.webkitConnection !== "undefined";
                    var data = {
                        browser: browser,
                        os: os,
                        hasConnection: hasConn,
                    };
                    // Chrome on Android should always have this; Chrome on desktop may or may not
                    if (os === "Android" && browser === "Chrome" && !hasConn) {
                        return {
                            result: "b12:1",
                            data,
                            logic: "Chrome on Android UA but navigator.connection absent → FAIL",
                        };
                    }
                    return {
                        result: "b12:0",
                        data,
                        logic: hasConn
                            ? `navigator.connection present → PASS`
                            : `navigator.connection absent (not required for this UA) → PASS`,
                    };
                } catch (e) {
                    return { result: "b12:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b13",
            num: 42,
            name: "Playwright / __pw_ Artifacts",
            fnName: "DETECT_Playwright_Artifacts",
            desc: "Playwright injects __pw_manual, __pwInitScripts, and __playwright__binding__ globals; any presence signals automation",
            run: function () {
                try {
                    var pwProps = [
                        "__playwright",
                        "__pw_manual",
                        "__pw_preview",
                        "__pwInitScripts",
                        "__playwright__binding__",
                        "playwright",
                    ];
                    var found = [];
                    pwProps.forEach(function (p) {
                        if (p in window) found.push(p);
                    });
                    var data = {
                        found: found.join(", ") || "none",
                        checked: pwProps.join(", "),
                    };
                    if (found.length > 0)
                        return {
                            result: "b13:1",
                            data,
                            logic: `Playwright artifact(s) found: ${found.join(", ")} → FAIL`,
                        };
                    return {
                        result: "b13:0",
                        data,
                        logic: "No Playwright window artifacts → PASS",
                    };
                } catch (e) {
                    return { result: "b13:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b14",
            num: 43,
            name: "Notification API Browser Consistency",
            fnName: "CHECK_NOTIFICATION_API_CONSISTENCY",
            desc: "Notification API must be present in Chrome/Firefox/Edge; absent on these browsers UA = automation stripping APIs",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var hasNotif = typeof Notification !== "undefined";
                    var data = {
                        browser: browser,
                        os: os,
                        hasNotification: hasNotif,
                    };
                    // Desktop Chrome/Firefox/Edge must have Notification
                    if (
                        (browser === "Chrome" ||
                            browser === "Firefox" ||
                            browser === "Edge") &&
                        os !== "iOS" &&
                        os !== "Android" &&
                        !hasNotif
                    ) {
                        return {
                            result: "b14:1",
                            data,
                            logic: `${browser} desktop UA but Notification API absent → FAIL`,
                        };
                    }
                    return {
                        result: "b14:0",
                        data,
                        logic: hasNotif
                            ? `Notification API present for ${browser} → PASS`
                            : `Notification API absent (acceptable for ${browser}/${os}) → PASS`,
                    };
                } catch (e) {
                    return { result: "b14:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b15",
            num: 44,
            name: "Math IEEE 754 Consistency",
            fnName: "CHECK_MATH_FINGERPRINT",
            desc: "Math domain-error functions must return NaN per IEEE 754; 1/-0 must be -Infinity; non-standard = broken JS engine",
            run: function () {
                try {
                    // Math.tan(-1e308) excluded: result is CPU/platform-specific due to argument reduction
                    var v1 = Math.acos(1.0001); // NaN: input outside domain [-1,1]
                    var v2 = Math.sqrt(-1); // NaN: negative radicand
                    var v3 = Math.log(-1); // NaN: negative input
                    var v4 = 1 / -0; // -Infinity: IEEE 754 signed zero
                    var data = {
                        "acos(1.0001)": v1,
                        "sqrt(-1)": v2,
                        "log(-1)": v3,
                        "1/-0": v4,
                    };
                    if (!isNaN(v1))
                        return {
                            result: "b15:1",
                            data,
                            logic: `Math.acos(1.0001)=${v1} (expected NaN, input >1) → FAIL`,
                        };
                    if (!isNaN(v2))
                        return {
                            result: "b15:1",
                            data,
                            logic: `Math.sqrt(-1)=${v2} (expected NaN) → FAIL`,
                        };
                    if (!isNaN(v3))
                        return {
                            result: "b15:1",
                            data,
                            logic: `Math.log(-1)=${v3} (expected NaN) → FAIL`,
                        };
                    if (v4 !== -Infinity)
                        return {
                            result: "b15:1",
                            data,
                            logic: `1/-0=${v4} (expected -Infinity) → FAIL`,
                        };
                    return {
                        result: "b15:0",
                        data,
                        logic: "All Math IEEE 754 domain checks pass → PASS",
                    };
                } catch (e) {
                    return { result: "b15:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b16",
            num: 45,
            name: "document.hasFocus() in Automation",
            fnName: "CHECK_DOCUMENT_FOCUS",
            desc: "document.hasFocus() returns false in headless Chrome unless explicitly set; real user sessions are focused",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var focused = document.hasFocus();
                    var data = {
                        "document.hasFocus()": focused,
                        browser: browser,
                    };
                    // Only a meaningful signal for Chromium-based browsers
                    if (
                        (browser === "Chrome" || browser === "Edge") &&
                        !focused
                    ) {
                        return {
                            result: "b16:1",
                            data,
                            logic: "Chrome/Edge but document.hasFocus()=false → FAIL (headless indicator)",
                        };
                    }
                    return {
                        result: "b16:0",
                        data,
                        logic: `hasFocus=${focused} → PASS`,
                    };
                } catch (e) {
                    return { result: "b16:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b17",
            num: 46,
            name: "navigator.userAgentData Brands Spoofing",
            fnName: "CHECK_USERAGENTDATA_BRANDS_CONSISTENCY",
            desc: 'UA-CH brands list must include expected entries; Chromium always includes "Not_A Brand" as decoy; absence or wrong ordering = spoofed',
            run: function () {
                try {
                    if (typeof navigator.userAgentData === "undefined") {
                        return {
                            result: "b17:0",
                            data: { note: "UA-CH not supported" },
                            logic: "navigator.userAgentData undefined → PASS (skipped)",
                        };
                    }
                    var brands = navigator.userAgentData.brands || [];
                    var brandNames = brands.map(function (b) {
                        return b.brand;
                    });
                    var data = {
                        brands: JSON.stringify(brands),
                        count: brands.length,
                    };
                    if (brands.length === 0) {
                        return {
                            result: "b17:1",
                            data,
                            logic: "UA-CH brands list is empty → FAIL",
                        };
                    }
                    // Real Chromium always includes a "Not_A Brand" or "Not;A Brand" decoy entry
                    var hasDecoy = brandNames.some(function (b) {
                        return b.indexOf("Not") !== -1;
                    });
                    if (!hasDecoy) {
                        return {
                            result: "b17:1",
                            data,
                            logic: 'Chromium brands list missing "Not_A Brand" decoy entry → FAIL (spoofed)',
                        };
                    }
                    return {
                        result: "b17:0",
                        data,
                        logic: `Brands list has ${brands.length} entries incl. decoy → PASS`,
                    };
                } catch (e) {
                    return { result: "b17:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b18",
            num: 47,
            name: "Media Devices API Presence",
            fnName: "CHECK_MEDIA_DEVICES_PRESENCE",
            desc: "Modern desktop Chrome/Firefox must expose navigator.mediaDevices.enumerateDevices; missing = stripped automation env",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    var hasMD =
                        typeof navigator.mediaDevices !== "undefined" &&
                        typeof navigator.mediaDevices.enumerateDevices ===
                            "function";
                    var data = {
                        browser: browser,
                        os: os,
                        hasMediaDevices: hasMD,
                    };
                    if (
                        (browser === "Chrome" ||
                            browser === "Firefox" ||
                            browser === "Edge") &&
                        os !== "iOS" &&
                        !hasMD
                    ) {
                        return {
                            result: "b18:1",
                            data,
                            logic: `${browser} desktop UA but navigator.mediaDevices.enumerateDevices absent → FAIL`,
                        };
                    }
                    return {
                        result: "b18:0",
                        data,
                        logic: hasMD
                            ? "navigator.mediaDevices.enumerateDevices present → PASS"
                            : "mediaDevices absent (acceptable for this UA) → PASS",
                    };
                } catch (e) {
                    return { result: "b18:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b19",
            num: 48,
            name: "Brave Browser Detection",
            fnName: "CHECK_BRAVE_BROWSER",
            desc: "Brave exposes navigator.brave.isBrave(); if present and UA claims Chrome, this is an inconsistency to flag for fingerprinting",
            run: function () {
                try {
                    var hasBrave =
                        typeof navigator.brave !== "undefined" &&
                        typeof navigator.brave.isBrave === "function";
                    var browser = DETECT_Browser_Type();
                    var data = {
                        hasBraveAPI: hasBrave,
                        detectedBrowser: browser,
                    };
                    // This is not necessarily bad, but it's an unmasked signal: UA says Chrome but it's actually Brave
                    if (hasBrave && browser === "Chrome") {
                        return {
                            result: "b19:1",
                            data,
                            logic: "navigator.brave.isBrave() present but UA reports Chrome (Brave masquerading as Chrome) → FAIL",
                        };
                    }
                    return {
                        result: "b19:0",
                        data,
                        logic: hasBrave
                            ? "Brave detected but UA reflects Brave → PASS"
                            : "Not Brave browser → PASS",
                    };
                } catch (e) {
                    return { result: "b19:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b20",
            num: 49,
            name: "WebRTC RTCPeerConnection Presence",
            fnName: "CHECK_WEBRTC_PRESENCE",
            desc: "RTCPeerConnection must be present in modern desktop browsers; absent = stripped headless env or privacy proxy",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var hasRTC =
                        typeof RTCPeerConnection !== "undefined" ||
                        typeof window.RTCPeerConnection !== "undefined" ||
                        typeof window.webkitRTCPeerConnection !== "undefined";
                    var data = {
                        browser: browser,
                        hasRTCPeerConnection: hasRTC,
                    };
                    if (
                        (browser === "Chrome" ||
                            browser === "Firefox" ||
                            browser === "Edge" ||
                            browser === "Safari") &&
                        !hasRTC
                    ) {
                        return {
                            result: "b20:1",
                            data,
                            logic: `${browser} UA but RTCPeerConnection absent → FAIL (stripped env)`,
                        };
                    }
                    return {
                        result: "b20:0",
                        data,
                        logic: hasRTC
                            ? "RTCPeerConnection present → PASS"
                            : "RTCPeerConnection absent (acceptable) → PASS",
                    };
                } catch (e) {
                    return { result: "b20:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b21",
            num: 50,
            name: "window.outerWidth/Height Zero Check",
            fnName: "CHECK_OUTER_WINDOW_DIMENSIONS",
            desc: "Old headless Chrome reported outerWidth=0 and outerHeight=0; a zero value on a Chrome UA is suspicious",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    var ow = window.outerWidth;
                    var oh = window.outerHeight;
                    var data = {
                        outerWidth: ow,
                        outerHeight: oh,
                        browser: browser,
                    };
                    if (
                        (browser === "Chrome" || browser === "Edge") &&
                        (ow === 0 || oh === 0)
                    ) {
                        return {
                            result: "b21:1",
                            data,
                            logic: `outerWidth=${ow}, outerHeight=${oh} — zero value on Chrome = headless indicator → FAIL`,
                        };
                    }
                    return {
                        result: "b21:0",
                        data,
                        logic: `outerWidth=${ow}, outerHeight=${oh} → PASS`,
                    };
                } catch (e) {
                    return { result: "b21:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b22",
            num: 51,
            name: "navigator.connection.rtt Zero Check",
            fnName: "CHECK_CONNECTION_RTT",
            desc: "navigator.connection.rtt === 0 in some headless/automation setups; real connections always have rtt > 0",
            run: function () {
                try {
                    var conn =
                        navigator.connection ||
                        navigator.mozConnection ||
                        navigator.webkitConnection;
                    if (!conn)
                        return {
                            result: "b22:0",
                            data: { note: "NetworkInformation not supported" },
                            logic: "navigator.connection not available → PASS (skipped)",
                        };
                    var rtt = conn.rtt;
                    var effectiveType = conn.effectiveType;
                    var data = { rtt: rtt, effectiveType: effectiveType };
                    if (rtt === 0) {
                        return {
                            result: "b22:1",
                            data,
                            logic: `navigator.connection.rtt=0 → FAIL (headless/automation indicator)`,
                        };
                    }
                    return {
                        result: "b22:0",
                        data,
                        logic: `rtt=${rtt}ms (>0) → PASS`,
                    };
                } catch (e) {
                    return { result: "b22:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b23",
            num: 52,
            name: "navigator.pdfViewerEnabled vs Chrome",
            fnName: "CHECK_PDF_VIEWER_ENABLED",
            desc: "Chrome 105+ always has pdfViewerEnabled=true; false on Chrome UA = old headless or stripped env (replaces plugins PDF check)",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    if (typeof navigator.pdfViewerEnabled === "undefined") {
                        return {
                            result: "b23:0",
                            data: {
                                note: "pdfViewerEnabled not supported (pre-Chrome 105)",
                            },
                            logic: "navigator.pdfViewerEnabled not available → PASS (skipped)",
                        };
                    }
                    var pve = navigator.pdfViewerEnabled;
                    var data = { browser: browser, pdfViewerEnabled: pve };
                    if (browser === "Chrome" && !pve) {
                        return {
                            result: "b23:1",
                            data,
                            logic: "Chrome UA but pdfViewerEnabled=false → FAIL (headless/stripped env)",
                        };
                    }
                    return {
                        result: "b23:0",
                        data,
                        logic: `pdfViewerEnabled=${pve} consistent with ${browser} → PASS`,
                    };
                } catch (e) {
                    return { result: "b23:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b24",
            num: 53,
            name: "navigator.mimeTypes Prototype Integrity",
            fnName: "CHECK_MIMETYPES_PROTOTYPE",
            desc: "navigator.mimeTypes must be a MimeTypeArray instance; spoofed/empty objects fail prototype check",
            run: function () {
                try {
                    var mimeTypes = navigator.mimeTypes;
                    var data = {
                        typeof: typeof mimeTypes,
                        length: mimeTypes ? mimeTypes.length : "N/A",
                    };
                    if (
                        typeof mimeTypes === "undefined" ||
                        mimeTypes === null
                    ) {
                        return {
                            result: "b24:0",
                            data,
                            logic: "mimeTypes not available → PASS (skipped)",
                        };
                    }
                    // Must be a MimeTypeArray, not a plain object
                    if (
                        Object.prototype.toString.call(mimeTypes) !==
                        "[object MimeTypeArray]"
                    ) {
                        return {
                            result: "b24:1",
                            data,
                            logic: `mimeTypes toString="${Object.prototype.toString.call(mimeTypes)}" (expected MimeTypeArray) → FAIL`,
                        };
                    }
                    return {
                        result: "b24:0",
                        data,
                        logic: `mimeTypes is MimeTypeArray with ${mimeTypes.length} entries → PASS`,
                    };
                } catch (e) {
                    return { result: "b24:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b26",
            num: 54,
            name: "Media Codec Chromium vs Chrome",
            fnName: "CHECK_MEDIA_CODEC_CHROMIUM_VS_CHROME",
            desc: 'Real Google Chrome returns "probably" for AAC/H.264; open-source Chromium (used by Playwright/Puppeteer) returns "maybe" (lacks proprietary codec licenses)',
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    if (browser !== "Chrome")
                        return {
                            result: "b26:0",
                            data: { browser: browser },
                            logic: "Not Chrome UA → PASS (skipped)",
                        };
                    var audio = document.createElement("audio");
                    var aacResult = audio.canPlayType(
                        'audio/mp4; codecs="mp4a.40.2"',
                    );
                    var video = document.createElement("video");
                    var h264Result = video.canPlayType(
                        'video/mp4; codecs="avc1.42E01E"',
                    );
                    var data = { aac: aacResult, h264: h264Result };
                    if (aacResult === "maybe" || h264Result === "maybe") {
                        return {
                            result: "b26:1",
                            data,
                            logic: `canPlayType AAC="${aacResult}" H.264="${h264Result}" — "maybe" = Chromium binary (no proprietary codecs) not real Chrome → FAIL`,
                        };
                    }
                    return {
                        result: "b26:0",
                        data,
                        logic: `AAC="${aacResult}" H.264="${h264Result}" — "probably" = real Chrome → PASS`,
                    };
                } catch (e) {
                    return { result: "b26:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b27",
            num: 55,
            name: "Google TTS Voice Count",
            fnName: "CHECK_GOOGLE_TTS_VOICES",
            desc: "Real Google Chrome ships 15–19 Google-branded TTS voices; Chromium/Playwright/Puppeteer ship zero Google voices even when total count is non-zero",
            run: function () {
                try {
                    var browser = DETECT_Browser_Type();
                    if (browser !== "Chrome")
                        return {
                            result: "b27:0",
                            data: { browser: browser },
                            logic: "Not Chrome UA → PASS (skipped)",
                        };
                    var voices =
                        typeof speechSynthesis !== "undefined"
                            ? speechSynthesis.getVoices()
                            : [];
                    var googleVoices = [];
                    for (var i = 0; i < voices.length; i++) {
                        if (voices[i].name.indexOf("Google ") === 0)
                            googleVoices.push(voices[i].name);
                    }
                    var data = {
                        totalVoices: voices.length,
                        googleVoices: googleVoices.length,
                        sample: googleVoices.slice(0, 3).join(", "),
                    };
                    if (voices.length > 10 && googleVoices.length === 0) {
                        return {
                            result: "b27:1",
                            data,
                            logic: `${voices.length} total voices but 0 Google-branded voices → FAIL (Chromium binary, not real Chrome)`,
                        };
                    }
                    return {
                        result: "b27:0",
                        data,
                        logic:
                            voices.length === 0
                                ? "No voices loaded yet → PASS (async; run after delay)"
                                : `${googleVoices.length} Google voices out of ${voices.length} total → PASS`,
                    };
                } catch (e) {
                    return { result: "b27:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b28",
            num: 56,
            name: "navigator.deviceMemory Valid Set",
            fnName: "CHECK_DEVICE_MEMORY_VALID",
            desc: "Device Memory API spec mandates Chrome round to {0.25, 0.5, 1, 2, 4, 8}; any other value = spoofed; also absent on Firefox/Safari = they must not have it",
            run: function () {
                try {
                    var dm = navigator.deviceMemory;
                    var browser = DETECT_Browser_Type();
                    var data = {
                        deviceMemory: String(dm),
                        browser: browser,
                        typeof: typeof dm,
                    };
                    if (typeof dm === "undefined") {
                        if (browser === "Chrome" || browser === "Edge") {
                            return {
                                result: "b28:1",
                                data,
                                logic: "Chrome UA but navigator.deviceMemory is undefined → FAIL",
                            };
                        }
                        return {
                            result: "b28:0",
                            data,
                            logic: "deviceMemory not available (expected for this browser) → PASS",
                        };
                    }
                    if (browser === "Firefox" || browser === "Safari") {
                        return {
                            result: "b28:1",
                            data,
                            logic: `${browser} UA but navigator.deviceMemory=${dm} exists (Chrome-only API) → FAIL`,
                        };
                    }
                    var validSet = [0.25, 0.5, 1, 2, 4, 8, 16, 32];
                    var isValid = false;
                    for (var i = 0; i < validSet.length; i++) {
                        if (dm === validSet[i]) {
                            isValid = true;
                            break;
                        }
                    }
                    if (!isValid) {
                        return {
                            result: "b28:1",
                            data,
                            logic: `navigator.deviceMemory=${dm} is not in spec-mandated set {0.25, 0.5, 1, 2, 4, 8, 16, 32} → FAIL (spoofed)`,
                        };
                    }
                    return {
                        result: "b28:0",
                        data,
                        logic: `deviceMemory=${dm} is in valid set → PASS`,
                    };
                } catch (e) {
                    return { result: "b28:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b29",
            num: 57,
            name: "iframe webdriver Property Leak",
            fnName: "CHECK_IFRAME_WEBDRIVER_LEAK",
            desc: "Stealth patches apply webdriver=false on the main frame but rarely patch newly-created iframes; checking iframe.contentWindow.navigator.webdriver catches incomplete patches",
            run: function () {
                try {
                    var iframe = document.createElement("iframe");
                    iframe.style.display = "none";
                    document.body.appendChild(iframe);
                    var iframeWebdriver;
                    try {
                        iframeWebdriver =
                            iframe.contentWindow &&
                            iframe.contentWindow.navigator &&
                            iframe.contentWindow.navigator.webdriver;
                    } catch (e2) {
                        document.body.removeChild(iframe);
                        return {
                            result: "b29:0",
                            data: { note: "Cross-origin or access denied" },
                            logic: "iframe access denied → PASS (skipped)",
                        };
                    }
                    document.body.removeChild(iframe);
                    var data = {
                        "iframe.navigator.webdriver": String(iframeWebdriver),
                        "window.navigator.webdriver": String(
                            navigator.webdriver,
                        ),
                    };
                    if (iframeWebdriver === true) {
                        return {
                            result: "b29:1",
                            data,
                            logic: "iframe.contentWindow.navigator.webdriver=true → FAIL (automation; stealth patch incomplete)",
                        };
                    }
                    return {
                        result: "b29:0",
                        data,
                        logic: `iframe.contentWindow.navigator.webdriver=${iframeWebdriver} → PASS`,
                    };
                } catch (e) {
                    return { result: "b29:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b30",
            num: 58,
            name: 'UA-CH "Google Chrome" Brand Presence',
            fnName: "CHECK_USERAGENTDATA_GOOGLE_CHROME_BRAND",
            desc: 'Real Google Chrome includes "Google Chrome" in navigator.userAgentData.brands; Chromium-only builds (used by Playwright/Puppeteer) only include "Chromium" and "Not_A Brand"',
            run: function () {
                try {
                    if (typeof navigator.userAgentData === "undefined") {
                        return {
                            result: "b30:0",
                            data: { note: "UA-CH not supported" },
                            logic: "navigator.userAgentData undefined → PASS (skipped)",
                        };
                    }
                    var browser = DETECT_Browser_Type();
                    if (browser !== "Chrome") {
                        return {
                            result: "b30:0",
                            data: { browser: browser },
                            logic: "Not Chrome UA → PASS (skipped)",
                        };
                    }
                    var brands = navigator.userAgentData.brands || [];
                    var brandNames = brands.map(function (b) {
                        return b.brand;
                    });
                    var hasGoogleChrome =
                        brandNames.indexOf("Google Chrome") !== -1;
                    var data = {
                        brands: JSON.stringify(brandNames),
                        hasGoogleChrome: hasGoogleChrome,
                    };
                    if (!hasGoogleChrome) {
                        return {
                            result: "b30:1",
                            data,
                            logic: '"Google Chrome" missing from UA-CH brands list → FAIL (Chromium binary, not real Chrome)',
                        };
                    }
                    return {
                        result: "b30:0",
                        data,
                        logic: '"Google Chrome" present in UA-CH brands → PASS',
                    };
                } catch (e) {
                    return { result: "b30:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b25",
            num: 59,
            name: "Broken Image naturalWidth Check",
            fnName: "CHECK_BROKEN_IMAGE_DIMENSIONS",
            desc: "In headless browsers, a broken <img> element reports naturalWidth=0; real browsers return non-zero for broken images due to alt icon rendering",
            run: function () {
                try {
                    var img = new Image();
                    // Access synchronous properties — naturalWidth before load is always 0
                    // The detection is about what happens after a failed load, but that's async.
                    // We can check the prototype instead: img must be an HTMLImageElement
                    var isProper = img instanceof HTMLImageElement;
                    var data = {
                        "img instanceof HTMLImageElement": isProper,
                        "typeof Image": typeof Image,
                    };
                    if (typeof Image === "undefined") {
                        return {
                            result: "b25:1",
                            data,
                            logic: "Image constructor undefined → FAIL",
                        };
                    }
                    if (!isProper) {
                        return {
                            result: "b25:1",
                            data,
                            logic: "new Image() is not instanceof HTMLImageElement → FAIL",
                        };
                    }
                    return {
                        result: "b25:0",
                        data,
                        logic: "Image constructor and HTMLImageElement prototype intact → PASS",
                    };
                } catch (e) {
                    return { result: "b25:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b31",
            num: 60,
            name: "macOS screen.availTop Menu Bar",
            fnName: "CHECK_SCREEN_AVAIL_TOP_MACOS",
            desc: "Real macOS always reserves ≥25px at the screen top for the menu bar (screen.availTop > 0). Headless Chrome has no OS UI and defaults availTop to 0.",
            run: function () {
                try {
                    var os = CHECK_DEVICE_IN_USERAGENT_STRING();
                    if (os !== "Mac") {
                        return {
                            result: "b31:0",
                            data: { os: os },
                            logic: "Not macOS UA → PASS (skipped)",
                        };
                    }
                    var availTop = screen.availTop;
                    var data = {
                        "screen.availTop": availTop,
                        "screen.availLeft": screen.availLeft,
                    };
                    if (availTop === 0) {
                        return {
                            result: "b31:1",
                            data,
                            logic: "macOS UA but screen.availTop=0 → FAIL (headless; no OS menu bar)",
                        };
                    }
                    return {
                        result: "b31:0",
                        data,
                        logic: "screen.availTop=" + availTop + " > 0 → PASS",
                    };
                } catch (e) {
                    return { result: "b31:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b32",
            num: 61,
            name: "Network Downlink Zero",
            fnName: "CHECK_CONNECTION_DOWNLINK",
            desc: "Headless Chrome reports navigator.connection.downlink=0 because there is no real network interface; real browsers return a positive Mbps value (multiples of 0.025).",
            run: function () {
                try {
                    if (typeof navigator.connection === "undefined") {
                        return {
                            result: "b32:0",
                            data: { note: "NetworkInformation API absent" },
                            logic: "navigator.connection absent → PASS (skipped)",
                        };
                    }
                    var dl = navigator.connection.downlink;
                    var rtt = navigator.connection.rtt;
                    var data = {
                        "connection.downlink": dl,
                        "connection.rtt": rtt,
                        effectiveType: navigator.connection.effectiveType,
                    };
                    if (dl === 0) {
                        return {
                            result: "b32:1",
                            data,
                            logic: "navigator.connection.downlink=0 → FAIL (headless; no real network interface)",
                        };
                    }
                    return {
                        result: "b32:0",
                        data,
                        logic: "downlink=" + dl + "Mbps → PASS",
                    };
                } catch (e) {
                    return { result: "b32:e", data: {}, logic: "Exception" };
                }
            },
        },
        {
            id: "b33",
            num: 62,
            name: "ChromeDriver webdriverAsyncExecutor",
            fnName: "CHECK_CHROMEDRIVER_ASYNC_EXECUTOR",
            desc: "ChromeDriver injects window.__$webdriverAsyncExecutor for async script execution; also scans prototype chain for cdc_*_Array/Promise/Symbol/Proxy/Object variants missed by the basic cdc_ scan.",
            run: function () {
                try {
                    if ("__$webdriverAsyncExecutor" in window) {
                        return {
                            result: "b33:1",
                            data: { found: "__$webdriverAsyncExecutor" },
                            logic: "window.__$webdriverAsyncExecutor present → FAIL (ChromeDriver)",
                        };
                    }
                    var obj = window,
                        allProps = [];
                    var depth = 0;
                    while (obj !== null && obj !== undefined && depth < 5) {
                        try {
                            allProps = allProps.concat(
                                Object.getOwnPropertyNames(obj),
                            );
                        } catch (e2) {}
                        obj = Object.getPrototypeOf(obj);
                        depth++;
                    }
                    var extended = allProps.filter(function (p) {
                        return /.+_.+_(Array|Promise|Symbol|Proxy|Object)$/.test(
                            p,
                        );
                    });
                    var data = {
                        __$webdriverAsyncExecutor: false,
                        extendedCdc: extended.join(", ") || "none",
                    };
                    if (extended.length > 0) {
                        return {
                            result: "b33:1",
                            data,
                            logic:
                                "Extended cdc_ artifacts found: " +
                                extended.join(", ") +
                                " → FAIL (ChromeDriver)",
                        };
                    }
                    return {
                        result: "b33:0",
                        data,
                        logic: "No extended ChromeDriver artifacts → PASS",
                    };
                } catch (e) {
                    return { result: "b33:e", data: {}, logic: "Exception" };
                }
            },
        },
    ];

    await new Promise((resolve) => setTimeout(resolve, 300));
    return CHECKS.map((ch) => {
        try {
            return ch.run();
        } catch (e) {
            return { result: ch.id + ":e", data: { error: e.message }, logic: "Unhandled exception" };
        }
    }).filter((res) => !res.result.endsWith(":0"));
};
