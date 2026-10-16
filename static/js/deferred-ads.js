// Keep advertising available without competing with the initial page render.
(() => {
    function load() {
        const script = document.createElement('script');
        script.async = true;
        script.crossOrigin = 'anonymous';
        script.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-6122367965027230';
        document.head.appendChild(script);
    }
    function idle() {
        if ('requestIdleCallback' in window) requestIdleCallback(load, {timeout: 5000});
        else setTimeout(load, 2000);
    }
    if (document.readyState === 'complete') idle();
    else window.addEventListener('load', idle, {once: true});
})();
