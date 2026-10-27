# ADATVÉDELMI IRÁNYELVEK (PRIVACY POLICY)

*Utolsó frissítés: 2026. október*

A fejlesztő elkötelezett a felhasználók adatainak védelme és a maximális biztonság mellett a **Quest Bot v4.0** (a továbbiakban: "Bot") használata során. Ez az Adatvédelmi Tájékoztató bemutatja, hogyan kezeli a rendszer az adatokat a működés közben.

---

## 1. Kezelt Adatok és Hitelesítés
* **Mobil QR-Kódos Hitelesítés (Discord Remote Auth v2):** A rendszer nem kér be és nem tárol semmilyen jelszót, e-mail címet vagy manuális tokent. A bejelentkezés a Discord hivatalos mobilalkalmazásával beolvasott QR-kódon és a telefonon történő kifejezett felhasználói megerősítésen alapul.
* **Végpontok Közötti Titkosítás (RSA-2048):** A QR-kódos hitelesítés során a Discord Gateway és a bot közötti kommunikáció aszimmetrikus kriptográfiai kulcspárral titkosított csatornán fut.

## 2. Az Adatok Felhasználásának Célja
* Az átmeneti hozzáférés kizárólag a fiókodhoz tartozó Discord Questek (videók megtekintése, játékok szimulálása, hangcsatornás aktivitások) automatikus teljesítésére és a megszerzett jutalmak beváltására szolgál.
* A bot **soha nem fér hozzá és nem olvas** privát üzeneteket, barátlistát, e-mail címet, bankkártyaadatokat vagy fiókjelszót.

## 3. Adattárolás és Biztonság
* **Nincs Adatbázisban Való Tárolás (No Database Storage):** A rendszer semmilyen bejelentkezési adatot vagy tokent nem ment el adatbázisba, konfigurációs fájlba vagy külső felhőtárhelyre.
* **Kizárólag Ideiglenes Memóriahasználat (RAM-Only):** A munkamenethez szükséges adatok kizárólag a folyamat futásának idejére léteznek a szerver memóriájában (RAM). Amint a küldetések befejeződnek, vagy a munkamenet leáll, az adatok azonnal és véglegesen törlődnek a memóriából.
* **Biztonságos HTTPS/WSS Kapcsolat:** Minden hálózati kommunikáció titkosított TLS/SSL kapcsolaton keresztül zajlik a hivatalos Discord API szervereivel.

## 4. Harmadik Fél
A rendszer semmilyen adatot nem ad el, nem továbbít és nem oszt meg harmadik féllel semmilyen formában.

## 5. Felhasználói Ellenőrzés és Hozzáférés Visszavonása
A felhasználó bármikor jogosult megszakítani a munkamenetet:
1. A kijelölt csatornában vagy privát üzenetben kiadott `/stop_my_session` vagy `/stop` paranccsal.
2. A Discord fiók jelszavának módosításával vagy a Discord beállítások "Eszközök" (Devices) menüpontjában az aktív munkamenet azonnali bontásával.
