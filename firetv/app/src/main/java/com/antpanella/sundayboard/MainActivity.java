package com.antpanella.sundayboard;

import android.app.Activity;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/**
 * Sunday Board for Fire TV: one full-screen web view on the published Board.
 * The Board itself is unchanged and still updates itself from GitHub Pages;
 * this app adds what a TV needs: no start screen, screen kept on, the remote's
 * keys mapped to the Board's shortcuts, a cursor for Settings, and a retry
 * screen when the network is down.
 */
public class MainActivity extends Activity {

    static final String BOARD = "https://antpanella.github.io/NFLscoreboard/";
    static final int RETRY_MS = 15000;

    private WebView web;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private String tvJs = "";
    private long lastBack = 0;
    private boolean offline = false;

    private final Runnable retry = new Runnable() {
        @Override public void run() { offline = false; web.loadUrl(BOARD); }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        tvJs = readAsset("tv.js");

        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(10, 20, 16));
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);          // the Board keeps its settings in localStorage
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // alert sounds without a click
        s.setTextZoom(100);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUserAgentString(s.getUserAgentString() + " SundayBoardFireTV/" + BuildConfig.VERSION_NAME);

        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                if (url != null && url.startsWith(BOARD)) view.evaluateJavascript(tvJs, null);
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (Build.VERSION.SDK_INT >= 21 && req.isForMainFrame()) showOffline();
            }
            @SuppressWarnings("deprecation")
            @Override public void onReceivedError(WebView view, int code, String desc, String url) {
                if (url != null && url.startsWith(BOARD)) showOffline();
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return false;                   // everything opens in the board's own view
            }
        });

        setContentView(web);
        immersive();
        if (state != null) web.restoreState(state);
        else web.loadUrl(BOARD);
        Toast.makeText(this, "Menu ≡ Settings · arrows + OK pick a game · ⏪ ⏩ delay · ⏯ refresh", Toast.LENGTH_LONG).show();
    }

    private void showOffline() {
        if (offline) return;
        offline = true;
        String html = "<html><body style='margin:0;background:#0A1410;color:#F2F5F0;"
            + "font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh'>"
            + "<div style='text-align:center'><div style='font-size:44px;font-weight:700'>Can't reach the board</div>"
            + "<div style='font-size:24px;color:#8FA396;margin-top:16px'>Check the Fire TV's internet connection. Trying again every 15 seconds.</div>"
            + "</div></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
        handler.removeCallbacks(retry);
        handler.postDelayed(retry, RETRY_MS);
    }

    private void js(String code) { js(code, null); }
    private void js(String code, ValueCallback<String> cb) {
        web.evaluateJavascript("(function(){try{return " + code + ";}catch(e){return 'none';}})()", cb);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int k = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN && e.getRepeatCount() == 0;
        boolean press = e.getAction() == KeyEvent.ACTION_DOWN;     // arrows may repeat while held
        switch (k) {
            // Every remote key goes to the page script, so the web view never moves
            // its own focus or scrolls behind the cursor's back.
            case KeyEvent.KEYCODE_DPAD_UP:    if (press) js("window.__tv ? __tv.nav('up') : 'none'");    return true;
            case KeyEvent.KEYCODE_DPAD_DOWN:  if (press) js("window.__tv ? __tv.nav('down') : 'none'");  return true;
            case KeyEvent.KEYCODE_DPAD_LEFT:  if (press) js("window.__tv ? __tv.nav('left') : 'none'");  return true;
            case KeyEvent.KEYCODE_DPAD_RIGHT: if (press) js("window.__tv ? __tv.nav('right') : 'none'"); return true;
            case KeyEvent.KEYCODE_DPAD_CENTER:
            case KeyEvent.KEYCODE_ENTER:
            case KeyEvent.KEYCODE_NUMPAD_ENTER:
                if (down) ok();
                return true;
            case KeyEvent.KEYCODE_MENU:
                if (down) js("window.__tv ? __tv.menu() : 'none'");
                return true;
            case KeyEvent.KEYCODE_MEDIA_REWIND:
                if (down) js("window.__tv ? __tv.key('[') : 'none'");
                return true;
            case KeyEvent.KEYCODE_MEDIA_FAST_FORWARD:
                if (down) js("window.__tv ? __tv.key(']') : 'none'");
                return true;
            case KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE:
            case KeyEvent.KEYCODE_MEDIA_PLAY:
            case KeyEvent.KEYCODE_MEDIA_PAUSE:
                if (down) js("window.__tv ? __tv.key('r') : 'none'");
                return true;
            case KeyEvent.KEYCODE_BACK:
                if (down) back();
                return true;
            default:
                return super.dispatchKeyEvent(e);
        }
    }

    /** OK: the page says "text" when it focused a text box, and then the keyboard opens. */
    private void ok() {
        js("window.__tv ? __tv.ok() : 'none'", new ValueCallback<String>() {
            @Override public void onReceiveValue(String v) {
                if (v == null || !v.contains("text")) return;
                web.requestFocus();
                InputMethodManager imm = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
                if (imm != null) imm.showSoftInput(web, InputMethodManager.SHOW_IMPLICIT);
            }
        });
    }

    /** Back closes a picker, Settings or a spotlight first; on the plain board, twice to leave. */
    private void back() {
        if (offline) { finish(); return; }
        String url = web.getUrl();
        if (url != null && !url.equals(BOARD) && !url.startsWith(BOARD + "#") && !url.startsWith(BOARD + "index.html") && web.canGoBack()) {
            web.goBack();                       // e.g. the picks page, opened from Settings
            return;
        }
        js("window.__tv ? __tv.back() : 'none'", new ValueCallback<String>() {
            @Override public void onReceiveValue(String v) {
                if (v != null && v.contains("handled")) return;
                long now = System.currentTimeMillis();
                if (now - lastBack < 2500) { finish(); return; }
                lastBack = now;
                Toast.makeText(MainActivity.this, "Press Back again to leave the board", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void immersive() {
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override public void onWindowFocusChanged(boolean has) { super.onWindowFocusChanged(has); if (has) immersive(); }
    @Override protected void onResume() { super.onResume(); web.onResume(); web.resumeTimers(); }
    @Override protected void onPause() { web.onPause(); super.onPause(); }
    @Override protected void onSaveInstanceState(Bundle out) { super.onSaveInstanceState(out); web.saveState(out); }
    @Override protected void onDestroy() { handler.removeCallbacksAndMessages(null); web.destroy(); super.onDestroy(); }

    private String readAsset(String name) {
        try (InputStream in = getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] b = new byte[8192]; int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
            return out.toString("UTF-8");
        } catch (Exception ex) { return ""; }
    }
}
