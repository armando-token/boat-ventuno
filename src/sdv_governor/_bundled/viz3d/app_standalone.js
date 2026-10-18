/* viz3d/app_standalone.js — Authoritative connection state helper & Three.js Fincantieri 60m 3D engine loader (js/util.js, js/env.js, js/vessel.js, js/data.js, js/app.js). */
(function () {
  'use strict';
  var SDV = (window.SDV = window.SDV || {});

  SDV.setConnectionState = function (state, metaText) {
    SDV.apiState = SDV.apiState || {};
    SDV.apiState.connectionState = state;
    var staleBanner = document.getElementById('staleBanner');
    var staleBannerMeta = document.getElementById('staleBannerMeta');
    var chipLink = document.getElementById('chipLink');
    var isStale = state === 'STALE';
    if (staleBanner) {
      staleBanner.hidden = !isStale;
      if (isStale && staleBannerMeta && metaText) {
        staleBannerMeta.textContent = metaText;
      }
    }
    if (chipLink) {
      if (isStale) {
        chipLink.textContent = '● STALE / DESCONECTADO';
        chipLink.className = 'chip alarm';
      } else if (state === 'LIVE') {
        chipLink.textContent = '● LIVE GOVERNOR (' + (SDV.apiState.bridgeMode || 'LIVE_8765') + ')';
        chipLink.className = 'chip ok';
      }
    }
  };

  if (window.SDV && window.SDV.running && window.THREE) return;
  var basePrefix = (window.location && window.location.pathname === '/viz3d') ? '/viz3d/' : '';
  function load(srcs, done) {
    var i = 0;
    (function next() {
      if (i >= srcs.length) return;
      var s = document.createElement('script');
      s.src = srcs[i++];
      s.onload = done;
      s.onerror = function () { s.remove(); next(); };
      document.head.appendChild(s);
    })();
  }
  function seq(list, done) {
    var i = 0;
    (function next() {
      if (i >= list.length) return done();
      var s = document.createElement('script');
      s.src = basePrefix + list[i++];
      s.onload = next;
      document.head.appendChild(s);
    })();
  }
  load([
    'https://cdn.jsdelivr.net/npm/three@0.147.0/build/three.min.js',
    'https://unpkg.com/three@0.147.0/build/three.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/three.js/r147/three.min.js',
    basePrefix + 'vendor/three.min.js'
  ], function () {
    seq(['js/util.js', 'js/env.js', 'js/vessel.js', 'js/data.js', 'js/app.js'], function () {
      if (window.SDV && window.SDV.start) window.SDV.start();
    });
  });
})();
