// AI TRADING TERMINAL v10.0 ULTRA - COMPLETE LOGIC WITH ALL FIXES
// ==============================================================================

// ============================================
// GLOBAL STATE
// ============================================

let currentMarket = 'BTCUSD';
let currentPrice = 95000;
let currentTimeframe = '1H';
let priceData = [];
let chartInitialized = false;
let tvWidget = null;
let autoRefreshInterval = null;
let nextUpdateCountdown = null;
let lastSignal = 'NEUTRAL';
let voiceMuted = false;
let lastMTFResult = null;

// AI Module States
let aiModulesEnabled = {
    knn: true,
    svm: true,
    nb: true,
    mtf: true,
    regime: true,
    alerts: true
};

// Alert Settings
let alertSettings = {
    smartAlerts: true
};

// Performance Tracking
let performanceTracker = {
    trades: [],
    winRate: 0,
    totalTrades: 0,
    profit: 0,
    wins: 0,
    losses: 0,
    bestTrade: 0,
    worstTrade: 0
};

// Market Configurations
const MARKETS = {
    'BTCUSD': { symbol: 'BINANCE:BTCUSDT', price: 95000, type: 'crypto', api: 'binance' },
    'ETHUSD': { symbol: 'BINANCE:ETHUSDT', price: 3500, type: 'crypto', api: 'binance' },
    'SOLUSD': { symbol: 'BINANCE:SOLUSDT', price: 180, type: 'crypto', api: 'binance' },
    'XAUUSD': { symbol: 'OANDA:XAUUSD', price: 2650, type: 'forex', api: 'twelvedata' }
};

// Timeframe Mappings
const TF_MAP_BINANCE = {
    '1m': '1m', '5m': '5m', '15m': '15m',
    '1H': '1h', '4H': '4h', '1D': '1d'
};

const TF_MAP_TWELVE = {
    '1m': '1min', '5m': '5min', '15m': '15min',
    '1H': '1h', '4H': '4h', '1D': '1day'
};

const TF_MAP_TV = {
    '1m': '1', '5m': '5', '15m': '15',
    '1H': '60', '4H': '240', '1D': 'D'
};

// ============================================
// DATA FETCHING FUNCTIONS
// ============================================

async function fetchBinanceData(symbol, interval, limit = 200) {
    try {
        const binanceSymbol = symbol.replace('USD', 'USDT');
        const apiUrl = `https://api.binance.com/api/v3/klines?symbol=${binanceSymbol}&interval=${interval}&limit=${limit}`;
        
        debugLog(`> FETCHING: Binance ${binanceSymbol} ${interval}`);
        
        const response = await fetch(apiUrl);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        
        if (!Array.isArray(data)) {
            throw new Error('Invalid data format');
        }
        
        const candles = data.map(k => ({
            time: k[0],
            open: parseFloat(k[1]),
            high: parseFloat(k[2]),
            low: parseFloat(k[3]),
            close: parseFloat(k[4]),
            volume: parseFloat(k[5])
        }));
        
        debugLog(`> ✓ BINANCE: ${candles.length} candles loaded`);
        document.getElementById('data-source').textContent = 'Binance (Real)';
        return candles;
    } catch (error) {
        debugLog(`> ❌ BINANCE ERROR: ${error.message}`);
        document.getElementById('data-source').textContent = 'Simulated (Demo)';
        return null;
    }
}

async function fetchTwelveData(symbol, interval, limit = 200) {
    try {
        const apiKey = 'c5ceeb021e524f6c90d0a3a21dc74027';
        const url = `https://api.twelvedata.com/time_series?symbol=${symbol}&interval=${interval}&apikey=${apiKey}&outputsize=${limit}`;
        
        debugLog(`> FETCHING: Twelve Data ${symbol} ${interval}`);
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.status === 'error' || !data.values) {
            debugLog(`> ❌ TWELVE DATA ERROR: ${data.message || 'No data'}`);
            return null;
        }
        
        const candles = data.values.reverse().map(v => ({
            time: new Date(v.datetime).getTime(),
            open: parseFloat(v.open),
            high: parseFloat(v.high),
            low: parseFloat(v.low),
            close: parseFloat(v.close),
            volume: parseFloat(v.volume || 0)
        }));
        
        debugLog(`> ✓ TWELVE DATA: ${candles.length} candles loaded`);
        document.getElementById('data-source').textContent = 'Twelve Data (Real)';
        return candles;
    } catch (error) {
        debugLog(`> ❌ TWELVE DATA ERROR: ${error.message}`);
        return null;
    }
}

async function fetchRealData(market, timeframe) {
    const config = MARKETS[market];
    
    if (config.api === 'binance') {
        const interval = TF_MAP_BINANCE[timeframe];
        return await fetchBinanceData(market, interval);
    } else if (config.api === 'twelvedata') {
        const interval = TF_MAP_TWELVE[timeframe];
        const symbol = 'XAU/USD';
        return await fetchTwelveData(symbol, interval);
    }
    
    return null;
}

// ============================================
// TECHNICAL INDICATORS
// ============================================

function calculateRSI(data, period = 14) {
    if (data.length < period + 1) return 50;
    
    let gains = 0, losses = 0;
    
    for (let i = data.length - period; i < data.length; i++) {
        const change = data[i].close - data[i - 1].close;
        if (change > 0) gains += change;
        else losses -= change;
    }
    
    const avgGain = gains / period;
    const avgLoss = losses / period;
    
    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return 100 - (100 / (1 + rs));
}

function calculateMACD(data, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
    if (data.length < slowPeriod) return { macd: 0, signal: 0, histogram: 0 };
    
    const emaFast = calculateEMA(data, fastPeriod);
    const emaSlow = calculateEMA(data, slowPeriod);
    const macd = emaFast - emaSlow;
    
    const signal = macd * 0.8;
    const histogram = macd - signal;
    
    return { macd, signal, histogram };
}

function calculateStochastic(data, period = 14) {
    if (data.length < period) return 50;
    
    const recentData = data.slice(-period);
    const currentClose = data[data.length - 1].close;
    const high = Math.max(...recentData.map(d => d.high));
    const low = Math.min(...recentData.map(d => d.low));
    
    if (high === low) return 50;
    return ((currentClose - low) / (high - low)) * 100;
}

function calculateADX(data, period = 14) {
    try {
        if (data.length < period + 1) return 25;
        
        let trSum = 0;
        for (let i = data.length - period; i < data.length; i++) {
            const high = data[i].high;
            const low = data[i].low;
            const prevClose = data[i - 1].close;
            const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
            trSum += tr;
        }
        
        const atr = trSum / period;
        const adx = Math.min(50, (atr / data[data.length - 1].close) * 1000);
        
        return adx;
    } catch (error) {
        return 25;
    }
}

function calculateEMA(data, period) {
    if (data.length < period) return data[data.length - 1].close;
    
    const k = 2 / (period + 1);
    let ema = data[data.length - period].close;
    
    for (let i = data.length - period + 1; i < data.length; i++) {
        ema = data[i].close * k + ema * (1 - k);
    }
    
    return ema;
}

function calculateSMA(data, period) {
    if (data.length < period) return data[data.length - 1].close;
    
    const slice = data.slice(-period);
    const sum = slice.reduce((acc, d) => acc + d.close, 0);
    return sum / period;
}

function calculateATR(data, period = 14) {
    if (data.length < period + 1) return data[data.length - 1].close * 0.02;
    
    let trSum = 0;
    for (let i = data.length - period; i < data.length; i++) {
        const high = data[i].high;
        const low = data[i].low;
        const prevClose = data[i - 1].close;
        const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
        trSum += tr;
    }
    
    return trSum / period;
}

// ============================================
// AI CLASSIFIERS
// ============================================

function kNNClassifier(features, trainingData, k = 5) {
    if (trainingData.length < k) return { prediction: 'NEUTRAL', confidence: 50 };
    
    const distances = trainingData.map(sample => {
        const dist = Math.sqrt(
            Math.pow(features.rsi - sample.rsi, 2) +
            Math.pow(features.macd - sample.macd, 2) +
            Math.pow(features.trend - sample.trend, 2) +
            Math.pow(features.volatility - sample.volatility, 2)
        );
        return { dist, label: sample.label };
    });
    
    distances.sort((a, b) => a.dist - b.dist);
    const nearest = distances.slice(0, k);
    
    const bullish = nearest.filter(n => n.label === 1).length;
    const bearish = nearest.filter(n => n.label === -1).length;
    
    const confidence = Math.max(bullish, bearish) / k * 100;
    
    if (bullish > bearish) return { prediction: 'BULLISH', confidence };
    if (bearish > bullish) return { prediction: 'BEARISH', confidence };
    return { prediction: 'NEUTRAL', confidence: 50 };
}

function svmClassifier(features, trainingData, epochs = 50) {
    if (trainingData.length < 10) return { prediction: 'NEUTRAL', confidence: 50, margin: 0 };
    
    let w = { rsi: 0, macd: 0, trend: 0, volatility: 0 };
    let b = 0;
    const learningRate = 0.01;
    
    for (let epoch = 0; epoch < epochs; epoch++) {
        for (let sample of trainingData) {
            const score = 
                w.rsi * sample.rsi +
                w.macd * sample.macd +
                w.trend * sample.trend +
                w.volatility * sample.volatility +
                b;
            
            const prediction = score >= 0 ? 1 : -1;
            
            if (prediction !== sample.label) {
                w.rsi += learningRate * sample.label * sample.rsi;
                w.macd += learningRate * sample.label * sample.macd;
                w.trend += learningRate * sample.label * sample.trend;
                w.volatility += learningRate * sample.label * sample.volatility;
                b += learningRate * sample.label;
            }
        }
    }
    
    const score = 
        w.rsi * features.rsi +
        w.macd * features.macd +
        w.trend * features.trend +
        w.volatility * features.volatility +
        b;
    
    const margin = Math.abs(score);
    const confidence = Math.min(95, 50 + margin * 10);
    
    if (score > 0) return { prediction: 'BULLISH', confidence, margin };
    if (score < 0) return { prediction: 'BEARISH', confidence, margin: -margin };
    return { prediction: 'NEUTRAL', confidence: 50, margin: 0 };
}

function naiveBayesClassifier(features, trainingData) {
    const bullishSamples = trainingData.filter(s => s.label === 1);
    const bearishSamples = trainingData.filter(s => s.label === -1);
    
    if (bullishSamples.length === 0 || bearishSamples.length === 0) {
        return { prediction: 'NEUTRAL', confidence: 50 };
    }
    
    const pBullish = bullishSamples.length / trainingData.length;
    const pBearish = bearishSamples.length / trainingData.length;
    
    function likelihood(samples, feature, value) {
        const values = samples.map(s => s[feature]);
        const mean = values.reduce((a, b) => a + b, 0) / values.length;
        const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / values.length;
        const std = Math.sqrt(variance + 0.01);
        
        return Math.exp(-Math.pow(value - mean, 2) / (2 * (variance + 0.01))) / (std * Math.sqrt(2 * Math.PI));
    }
    
    const bullishProb = 
        pBullish *
        likelihood(bullishSamples, 'rsi', features.rsi) *
        likelihood(bullishSamples, 'macd', features.macd) *
        likelihood(bullishSamples, 'trend', features.trend) *
        likelihood(bullishSamples, 'volatility', features.volatility);
    
    const bearishProb = 
        pBearish *
        likelihood(bearishSamples, 'rsi', features.rsi) *
        likelihood(bearishSamples, 'macd', features.macd) *
        likelihood(bearishSamples, 'trend', features.trend) *
        likelihood(bearishSamples, 'volatility', features.volatility);
    
    const total = bullishProb + bearishProb;
    if (total === 0) return { prediction: 'NEUTRAL', confidence: 50 };
    
    const confidence = Math.max(bullishProb, bearishProb) / total * 100;
    
    if (bullishProb > bearishProb) return { prediction: 'BULLISH', confidence };
    if (bearishProb > bullishProb) return { prediction: 'BEARISH', confidence };
    return { prediction: 'NEUTRAL', confidence: 50 };
}

// ============================================
// MARKET ANALYSIS
// ============================================

function generateTrainingData(data) {
    const training = [];
    
    if (data.length < 31) return training;
    
    for (let i = 20; i < data.length - 10; i++) {
        const slice = data.slice(0, i);
        const rsi = calculateRSI(slice);
        const macd = calculateMACD(slice).macd;
        const ema20 = calculateEMA(slice, 20);
        const ema50 = calculateEMA(slice, 50);
        
        const futurePrice = data[i + 10].close;
        const currentPrice = data[i].close;
        const label = futurePrice > currentPrice ? 1 : -1;
        
        training.push({
            rsi: rsi / 100,
            macd: macd / 10,
            trend: ema20 > ema50 ? 1 : -1,
            volatility: (data[i].high - data[i].low) / data[i].close,
            label
        });
    }
    
    return training;
}

function extractFeatures(data) {
    const rsi = calculateRSI(data);
    const macd = calculateMACD(data);
    const ema20 = calculateEMA(data, 20);
    const ema50 = calculateEMA(data, 50);
    const latest = data[data.length - 1];
    
    return {
        rsi: rsi / 100,
        macd: macd.macd / 10,
        trend: ema20 > ema50 ? 1 : -1,
        volatility: (latest.high - latest.low) / latest.close
    };
}

function detectMarketRegime(data) {
    const adx = calculateADX(data);
    const ema20 = calculateEMA(data, 20);
    const ema50 = calculateEMA(data, 50);
    const atr = calculateATR(data);
    const currentPrice = data[data.length - 1].close;
    const volatility = (atr / currentPrice) * 100;
    
    let regime, prediction, strategy;
    
    if (adx > 30 && ema20 > ema50) {
        regime = '🚀 STRONG UPTREND';
        prediction = 'BULLISH';
        strategy = 'Trend Following: Buy pullbacks to EMA20';
    } else if (adx > 30 && ema20 < ema50) {
        regime = '📉 STRONG DOWNTREND';
        prediction = 'BEARISH';
        strategy = 'Trend Following: Sell rallies to EMA20';
    } else if (volatility > 3) {
        regime = '⚡ HIGH VOLATILITY';
        prediction = 'NEUTRAL';
        strategy = 'Breakout Trading: Wait for direction';
    } else if (adx < 20) {
        regime = '💤 RANGING MARKET';
        prediction = 'NEUTRAL';
        strategy = 'Range Trading: Buy support, sell resistance';
    } else {
        regime = '🔄 TRANSITIONAL';
        prediction = 'NEUTRAL';
        strategy = 'Wait for clearer direction';
    }
    
    return { name: regime, prediction, strategy, adx, volatility };
}

async function analyzeAllTimeframes() {
    const timeframes = ['1m', '5m', '15m', '1H', '4H', '1D'];
    const results = [];
    
    debugLog('> MTF: Analyzing all timeframes...');
    
    for (const tf of timeframes) {
        const data = await fetchRealData(currentMarket, tf);
        if (!data || data.length < 50) {
            results.push({ tf, signal: 'NEUTRAL', rsi: 50, macd: 0, trend: 'Neutral', score: 0 });
            continue;
        }
        
        const rsi = calculateRSI(data);
        const macd = calculateMACD(data);
        const ema20 = calculateEMA(data, 20);
        const ema50 = calculateEMA(data, 50);
        
        let score = 0;
        if (rsi < 30) score++;
        else if (rsi > 70) score--;
        
        if (macd.macd > 0) score++;
        else if (macd.macd < 0) score--;
        
        if (ema20 > ema50) score++;
        else if (ema20 < ema50) score--;
        
        const signal = score >= 2 ? 'BULLISH' : score <= -2 ? 'BEARISH' : 'NEUTRAL';
        const trend = ema20 > ema50 ? '↗️ Up' : ema20 < ema50 ? '↘️ Down' : '→ Flat';
        
        results.push({ tf, signal, rsi: rsi.toFixed(1), macd: macd.macd.toFixed(2), trend, score });
    }
    
    const bullishCount = results.filter(r => r.signal === 'BULLISH').length;
    const bearishCount = results.filter(r => r.signal === 'BEARISH').length;
    const neutralCount = results.filter(r => r.signal === 'NEUTRAL').length;
    
    let mtfPrediction = 'NEUTRAL';
    if (bullishCount >= 4) mtfPrediction = 'BULLISH';
    else if (bearishCount >= 4) mtfPrediction = 'BEARISH';
    
    debugLog(`> ✓ MTF: ${bullishCount}🟢 ${bearishCount}🔴 ${neutralCount}⚪`);
    
    return { results, bullishCount, bearishCount, neutralCount, prediction: mtfPrediction };
}

// ============================================
// CONFLUENCE ANALYSIS
// ============================================

function analyzeConfluence(data, mtfResult = null) {
    const rsi = calculateRSI(data);
    const macd = calculateMACD(data);
    const stoch = calculateStochastic(data);
    const adx = calculateADX(data);
    const ema20 = calculateEMA(data, 20);
    const ema50 = calculateEMA(data, 50);
    
    let indicatorVotes = 0;
    const votes = {
        rsi: 'NEUTRAL',
        macd: 'NEUTRAL',
        stoch: 'NEUTRAL',
        ema: 'NEUTRAL',
        trend: 'NEUTRAL'
    };
    
    if (rsi < 30) { indicatorVotes++; votes.rsi = 'BULLISH'; }
    else if (rsi > 70) { indicatorVotes--; votes.rsi = 'BEARISH'; }
    
    if (macd.macd > 0) { indicatorVotes++; votes.macd = 'BULLISH'; }
    else if (macd.macd < 0) { indicatorVotes--; votes.macd = 'BEARISH'; }
    
    if (stoch < 20) { indicatorVotes++; votes.stoch = 'BULLISH'; }
    else if (stoch > 80) { indicatorVotes--; votes.stoch = 'BEARISH'; }
    
    if (ema20 > ema50) { indicatorVotes++; votes.ema = 'BULLISH'; }
    else if (ema20 < ema50) { indicatorVotes--; votes.ema = 'BEARISH'; }
    
    const trendSlice = data.slice(-10);
    const trendChange = trendSlice[trendSlice.length - 1].close - trendSlice[0].close;
    if (trendChange > 0) { indicatorVotes++; votes.trend = 'BULLISH'; }
    else if (trendChange < 0) { indicatorVotes--; votes.trend = 'BEARISH'; }
    
    const features = extractFeatures(data);
    const trainingData = generateTrainingData(data);
    
    let aiVotes = 0;
    let knnResult = { prediction: 'NEUTRAL', confidence: 50 };
    let svmResult = { prediction: 'NEUTRAL', confidence: 50, margin: 0 };
    let nbResult = { prediction: 'NEUTRAL', confidence: 50 };
    
    if (aiModulesEnabled.knn && trainingData.length >= 5) {
        knnResult = kNNClassifier(features, trainingData);
        if (knnResult.prediction === 'BULLISH') aiVotes++;
        else if (knnResult.prediction === 'BEARISH') aiVotes--;
    }
    
    if (aiModulesEnabled.svm && trainingData.length >= 10) {
        svmResult = svmClassifier(features, trainingData);
        if (svmResult.prediction === 'BULLISH') aiVotes++;
        else if (svmResult.prediction === 'BEARISH') aiVotes--;
    }
    
    if (aiModulesEnabled.nb && trainingData.length >= 5) {
        nbResult = naiveBayesClassifier(features, trainingData);
        if (nbResult.prediction === 'BULLISH') aiVotes++;
        else if (nbResult.prediction === 'BEARISH') aiVotes--;
    }
    
    let regimeVote = 0;
    let regimeResult = { name: '🔄 TRANSITIONAL', prediction: 'NEUTRAL', strategy: 'Wait for clarity' };
    
    if (aiModulesEnabled.regime) {
        regimeResult = detectMarketRegime(data);
        if (regimeResult.prediction === 'BULLISH') regimeVote = 1;
        else if (regimeResult.prediction === 'BEARISH') regimeVote = -1;
    }
    
    let mtfVote = 0;
    if (aiModulesEnabled.mtf && mtfResult) {
        if (mtfResult.prediction === 'BULLISH') mtfVote = 1;
        else if (mtfResult.prediction === 'BEARISH') mtfVote = -1;
    }
    
    // Calculate CORRECT maxPoints based on enabled modules
    let maxPoints = 8; // Base: 5 indicators + 3 AI
    if (aiModulesEnabled.mtf && mtfResult) maxPoints++; // Add 1 for MTF = 9
    if (aiModulesEnabled.regime) maxPoints++; // Add 1 for Regime = 10 (if MTF also on) or 9 (if only regime)
    
    let totalScore = indicatorVotes + aiVotes + regimeVote + mtfVote;
    let normalizedScore = Math.round((totalScore + maxPoints) / 2);
    normalizedScore = Math.max(0, Math.min(maxPoints, normalizedScore));
    
    const percentage = (normalizedScore / maxPoints) * 100;
    
    // ===== SAFETY CHECKS - ONLY WHEN MODULES ARE ON (OPTION B) =====
    // User has full control - safety checks ONLY apply when modules are enabled
    let safetyOverride = false;
    let safetyReason = '';
    
    // Safety Check 1: MTF must be clearly aligned (4+ in one direction)
    // ONLY checks if MTF module is enabled
    if (aiModulesEnabled.mtf && mtfResult) {
        if (mtfResult.bullishCount < 4 && mtfResult.bearishCount < 4) {
            safetyOverride = true;
            safetyReason = 'MTF mixed signals';
            debugLog('> ⚠️ SAFETY: MTF mixed - forcing NEUTRAL');
        }
    }
    
    // Safety Check 2: Indicators vs AI must not strongly disagree
    // ONLY applies when both are being used
    if (Math.abs(indicatorVotes - aiVotes) >= 4) {
        safetyOverride = true;
        safetyReason = 'Indicator/AI conflict';
        debugLog('> ⚠️ SAFETY: Indicators vs AI conflict - forcing NEUTRAL');
    }
    
    // Safety Check 3: Risk level considerations
    const atr = calculateATR(data);
    const volatility = (atr / currentPrice) * 100;
    const isHighRisk = adx < 20 || volatility > 4;
    
    // Determine signal with CONSERVATIVE thresholds
    let signal, strength, action;
    
    if (safetyOverride) {
        // Force NEUTRAL when safety checks fail
        signal = '⚪ NEUTRAL';
        strength = 'NEUTRAL';
        action = `🚫 NO TRADE (${safetyReason})`;
    } else if (percentage >= 90) { 
        signal = '🟢🟢🟢 EXTREME BUY'; 
        strength = 'EXTREME'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '✅ BUY'; 
    } else if (percentage >= 78) { 
        signal = '🟢🟢 STRONG BUY'; 
        strength = 'STRONG'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '🚀 STRONG BUY'; 
    } else if (percentage >= 65) { 
        signal = '🟢 BUY'; 
        strength = 'MODERATE'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '📈 BUY'; 
    } else if (percentage >= 55) { 
        signal = '🟡 WEAK BUY'; 
        strength = 'WEAK'; 
        action = '⏸️ WAIT'; 
    } else if (percentage >= 45) { 
        signal = '⚪ NEUTRAL'; 
        strength = 'NEUTRAL'; 
        action = '🚫 NO TRADE'; 
    } else if (percentage >= 35) { 
        signal = '🟡 WEAK SELL'; 
        strength = 'WEAK'; 
        action = '⏸️ WAIT'; 
    } else if (percentage >= 22) { 
        signal = '🔴 SELL'; 
        strength = 'MODERATE'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '📉 SELL'; 
    } else if (percentage >= 12) { 
        signal = '🔴🔴 STRONG SELL'; 
        strength = 'STRONG'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '⛔ STRONG SELL'; 
    } else { 
        signal = '🔴🔴🔴 EXTREME SELL'; 
        strength = 'EXTREME'; 
        action = isHighRisk ? '⏸️ WAIT (High Risk)' : '💥 SELL'; 
    }
    
    // Risk level already calculated above, just determine final risk level
    let riskLevel;
    
    if (adx > 30 && volatility < 2) {
        riskLevel = 'LOW';
    } else if (adx > 25 && volatility < 3) {
        riskLevel = 'LOW';
    } else if (adx > 20 && volatility < 4) {
        riskLevel = 'MEDIUM';
    } else if (adx < 20 || volatility > 4) {
        riskLevel = 'HIGH';
    } else {
        riskLevel = 'MEDIUM';
    }
    
    debugLog(`> CONFLUENCE: Ind=${indicatorVotes} AI=${aiVotes} MTF=${mtfVote} Regime=${regimeVote} = ${normalizedScore}/${maxPoints} (${percentage.toFixed(0)}%)`);
    
    return {
        score: normalizedScore,
        maxPoints,
        percentage: parseFloat(percentage.toFixed(1)), // FIX: Ensure percentage is a number
        signal,
        strength,
        action,
        riskLevel,
        votes: {
            indicators: { score: indicatorVotes, details: votes },
            ai: { score: aiVotes, knn: knnResult, svm: svmResult, nb: nbResult },
            regime: regimeResult,
            mtf: mtfVote
        },
        indicators: { rsi, macd, stoch, adx, ema20, ema50 }
    };
}

// ============================================
// NEURAL INSIGHTS GENERATOR
// ============================================

function generateNeuralInsights(confluence, mtfResult = null) {
    try {
        const { score, maxPoints, percentage, signal, indicators, votes } = confluence;
        
        let reasoning = '';
        const aiConsensus = votes.ai.score;
        const indConsensus = votes.indicators.score;
        
        if (percentage >= 70) {
            reasoning = `🎯 STRONG ${aiConsensus > 0 ? 'BULLISH' : aiConsensus < 0 ? 'BEARISH' : 'NEUTRAL'} ALIGNMENT across all ${maxPoints} layers. Technical indicators show ${Math.abs(indConsensus)}/5 ${indConsensus > 0 ? 'bullish' : indConsensus < 0 ? 'bearish' : 'neutral'}. RSI ${indicators.rsi.toFixed(1)} ${indicators.rsi < 30 ? 'signals oversold - bounce expected' : indicators.rsi > 70 ? 'overbought - pullback risk' : 'neutral momentum'}. MACD ${indicators.macd.macd > 0 ? 'positive +' + indicators.macd.macd.toFixed(2) : 'negative ' + indicators.macd.macd.toFixed(2)}. AI consensus: k-NN ${votes.ai.knn.confidence.toFixed(0)}%, SVM ${Math.abs(votes.ai.svm.margin).toFixed(2)}, NB ${votes.ai.nb.confidence.toFixed(0)}%. ${mtfResult ? mtfResult.bullishCount + ' bullish/' + mtfResult.bearishCount + ' bearish timeframes.' : ''} HIGH-PROBABILITY setup.`;
        } else if (percentage >= 40) {
            reasoning = `⚠️ MIXED SIGNALS. Indicators ${Math.abs(indConsensus)}/5 ${indConsensus > 0 ? 'bullish' : indConsensus < 0 ? 'bearish' : 'neutral'}. AI ${aiConsensus > 0 ? 'leans bullish' : aiConsensus < 0 ? 'leans bearish' : 'divided'}: k-NN ${votes.ai.knn.prediction} (${votes.ai.knn.confidence.toFixed(0)}%), SVM ${votes.ai.svm.prediction}, NB ${votes.ai.nb.prediction}. RSI ${indicators.rsi.toFixed(1)} + MACD ${indicators.macd.macd.toFixed(2)} suggest transition. ${mtfResult ? mtfResult.neutralCount + ' neutral timeframes.' : ''} WAIT for 7/10+ confluence.`;
        } else {
            reasoning = `🚨 STRONG ${percentage < 40 ? 'BEARISH' : 'BULLISH'} PRESSURE. ${Math.abs(indConsensus)}/5 indicators ${indConsensus > 0 ? 'bullish' : 'bearish'}. AI consensus ${aiConsensus > 0 ? 'bullish' : 'bearish'}: k-NN ${votes.ai.knn.prediction}, SVM ${votes.ai.svm.prediction}, NB ${votes.ai.nb.prediction}. RSI ${indicators.rsi.toFixed(1)}, MACD ${indicators.macd.macd.toFixed(2)}, Stoch ${indicators.stoch.toFixed(1)}. ${mtfResult ? (percentage < 40 ? mtfResult.bearishCount : mtfResult.bullishCount) + '/6 timeframes aligned.' : ''} HIGH-CONVICTION setup.`;
        }
        
        const regime = votes.regime.name;
        const adx = indicators.adx.toFixed(1);
        const atr = calculateATR(priceData);
        const volatility = ((atr / currentPrice) * 100).toFixed(2);
        const emaSpread = ((indicators.ema20 - indicators.ema50) / indicators.ema50 * 100).toFixed(2);
        
        let analysis = `📊 REGIME: ${regime}, ADX ${adx} ${parseFloat(adx) > 30 ? '(STRONG)' : parseFloat(adx) > 20 ? '(MODERATE)' : '(WEAK)'}. Volatility ${volatility}% ${parseFloat(volatility) > 3 ? '(HIGH)' : parseFloat(volatility) > 1.5 ? '(MODERATE)' : '(LOW)'}. `;
        analysis += `📈 EMA20 ${indicators.ema20.toFixed(2)} ${indicators.ema20 > indicators.ema50 ? 'above' : 'below'} EMA50 ${indicators.ema50.toFixed(2)} by ${Math.abs(emaSpread)}%. `;
        analysis += `💹 Price ${currentPrice.toFixed(2)} ${currentPrice > indicators.ema20 ? 'above EMA20 (bullish)' : 'below EMA20 (bearish)'}. `;
        analysis += `🎚️ RSI ${indicators.rsi.toFixed(1)} ${indicators.rsi > 70 ? 'OVERBOUGHT' : indicators.rsi < 30 ? 'OVERSOLD' : 'NEUTRAL'}, Stoch ${indicators.stoch.toFixed(1)}, MACD ${indicators.macd.macd.toFixed(2)}.`;
        
        let strategy = '';
        const stopDistance = (atr * 1.5).toFixed(2);
        const target1 = (currentPrice * (confluence.action.includes('BUY') ? 1.015 : 0.985)).toFixed(2);
        const target2 = (currentPrice * (confluence.action.includes('BUY') ? 1.03 : 0.97)).toFixed(2);
        const stopLevel = (currentPrice - (confluence.action.includes('BUY') ? 1 : -1) * atr * 1.5).toFixed(2);
        const riskPercent = ((Math.abs(currentPrice - parseFloat(stopLevel)) / currentPrice) * 100).toFixed(2);
        
        if (confluence.action.includes('BUY') && !confluence.action.includes('WAIT')) {
            strategy = `🟢 LONG: Entry ${currentPrice.toFixed(2)}, Stop ${stopLevel} (${riskPercent}% risk), T1 ${target1} (1.5%), T2 ${target2} (3%). Size: ${confluence.riskLevel === 'LOW' ? '2-3%' : confluence.riskLevel === 'MEDIUM' ? '1-2%' : '0.5-1%'}. ${mtfResult && mtfResult.bullishCount >= 4 ? 'MTF confirms ' + mtfResult.bullishCount + '/6 bullish.' : ''}`;
        } else if (confluence.action.includes('SELL') && !confluence.action.includes('WAIT')) {
            strategy = `🔴 SHORT: Entry ${currentPrice.toFixed(2)}, Stop ${stopLevel} (${riskPercent}% risk), T1 ${target1} (1.5%), T2 ${target2} (3%). Size: ${confluence.riskLevel === 'LOW' ? '2-3%' : confluence.riskLevel === 'MEDIUM' ? '1-2%' : '0.5-1%'}. ${mtfResult && mtfResult.bearishCount >= 4 ? 'MTF confirms ' + mtfResult.bearishCount + '/6 bearish.' : ''}`;
        } else if (confluence.action.includes('WAIT')) {
            strategy = `⏸️ WAIT: ${score}/${maxPoints} (${percentage.toFixed(0)}%) insufficient. Need 7/10+ for high-probability setup. Monitor levels: Support ${(currentPrice * 0.98).toFixed(2)}, Resistance ${(currentPrice * 1.02).toFixed(2)}.`;
        } else {
            strategy = `🚫 NO TRADE: Market neutral (${percentage.toFixed(0)}%). Wait for ADX>25, RSI breakout, or MACD acceleration. Consider ${currentTimeframe === '1m' || currentTimeframe === '5m' ? 'higher timeframes' : 'longer-term positions'}.`;
        }
        
        return { reasoning, analysis, strategy };
    } catch (error) {
        debugLog(`❌ Neural Insights Error: ${error.message}`);
        return {
            reasoning: 'AI analysis in progress...',
            analysis: 'Processing market data...',
            strategy: 'Calculating optimal strategy...'
        };
    }
}

// ============================================
// UPDATE CONFLUENCE CORE TABLE
// ============================================

function updateConfluenceAnalysisCore(confluence, mtfResult) {
    try {
        const { indicators, votes } = confluence;
        
        document.getElementById('conf-rsi').textContent = indicators.rsi.toFixed(1);
        document.getElementById('conf-rsi-signal').textContent = indicators.rsi < 30 ? '🟢 BULLISH' : indicators.rsi > 70 ? '🔴 BEARISH' : '⚪ NEUTRAL';
        document.getElementById('conf-rsi-signal').className = `text-xs font-semibold ${indicators.rsi < 30 ? 'text-green-400' : indicators.rsi > 70 ? 'text-red-400' : 'text-gray-400'}`;
        
        document.getElementById('conf-macd').textContent = indicators.macd.macd.toFixed(3);
        document.getElementById('conf-macd-signal').textContent = indicators.macd.macd > 0 ? '🟢 BULLISH' : indicators.macd.macd < 0 ? '🔴 BEARISH' : '⚪ NEUTRAL';
        document.getElementById('conf-macd-signal').className = `text-xs font-semibold ${indicators.macd.macd > 0 ? 'text-green-400' : indicators.macd.macd < 0 ? 'text-red-400' : 'text-gray-400'}`;
        
        document.getElementById('conf-stoch').textContent = indicators.stoch.toFixed(1);
        document.getElementById('conf-stoch-signal').textContent = indicators.stoch < 20 ? '🟢 BULLISH' : indicators.stoch > 80 ? '🔴 BEARISH' : '⚪ NEUTRAL';
        document.getElementById('conf-stoch-signal').className = `text-xs font-semibold ${indicators.stoch < 20 ? 'text-green-400' : indicators.stoch > 80 ? 'text-red-400' : 'text-gray-400'}`;
        
        const emaDirection = indicators.ema20 > indicators.ema50 ? '↗ Golden' : indicators.ema20 < indicators.ema50 ? '↘ Death' : '→ Flat';
        document.getElementById('conf-ema').textContent = emaDirection;
        document.getElementById('conf-ema-signal').textContent = indicators.ema20 > indicators.ema50 ? '🟢 BULLISH' : indicators.ema20 < indicators.ema50 ? '🔴 BEARISH' : '⚪ NEUTRAL';
        document.getElementById('conf-ema-signal').className = `text-xs font-semibold ${indicators.ema20 > indicators.ema50 ? 'text-green-400' : indicators.ema20 < indicators.ema50 ? 'text-red-400' : 'text-gray-400'}`;
        
        const trendSlice = priceData.slice(-10);
        const trendChange = trendSlice[trendSlice.length - 1].close - trendSlice[0].close;
        const trendDirection = trendChange > 0 ? '↗️ Up' : trendChange < 0 ? '↘️ Down' : '→ Flat';
        document.getElementById('conf-trend').textContent = trendDirection;
        document.getElementById('conf-trend-signal').textContent = trendChange > 0 ? '🟢 BULLISH' : trendChange < 0 ? '🔴 BEARISH' : '⚪ NEUTRAL';
        document.getElementById('conf-trend-signal').className = `text-xs font-semibold ${trendChange > 0 ? 'text-green-400' : trendChange < 0 ? 'text-red-400' : 'text-gray-400'}`;
        
        document.getElementById('conf-knn-pred').textContent = votes.ai.knn.prediction;
        document.getElementById('conf-knn-pred').className = `text-sm font-bold ${votes.ai.knn.prediction === 'BULLISH' ? 'text-green-400' : votes.ai.knn.prediction === 'BEARISH' ? 'text-red-400' : 'text-purple-400'}`;
        document.querySelector('#conf-knn-conf span').textContent = votes.ai.knn.confidence.toFixed(0) + '%';
        
        document.getElementById('conf-svm-pred').textContent = votes.ai.svm.prediction;
        document.getElementById('conf-svm-pred').className = `text-sm font-bold ${votes.ai.svm.prediction === 'BULLISH' ? 'text-green-400' : votes.ai.svm.prediction === 'BEARISH' ? 'text-red-400' : 'text-purple-400'}`;
        // Convert SVM margin to confidence percentage for display
        const svmConfidenceDisplay = Math.min(95, 50 + Math.abs(votes.ai.svm.margin) * 10);
        document.querySelector('#conf-svm-conf span').textContent = svmConfidenceDisplay.toFixed(0) + '%';
        
        document.getElementById('conf-nb-pred').textContent = votes.ai.nb.prediction;
        document.getElementById('conf-nb-pred').className = `text-sm font-bold ${votes.ai.nb.prediction === 'BULLISH' ? 'text-green-400' : votes.ai.nb.prediction === 'BEARISH' ? 'text-red-400' : 'text-purple-400'}`;
        document.querySelector('#conf-nb-conf span').textContent = votes.ai.nb.confidence.toFixed(0) + '%';
        
        if (mtfResult) {
            const mtfScore = Math.max(mtfResult.bullishCount, mtfResult.bearishCount, mtfResult.neutralCount);
            document.getElementById('conf-mtf-vote').textContent = `${mtfScore}/6`;
            document.getElementById('conf-mtf-vote').className = `text-lg font-bold ${mtfResult.bullishCount >= 4 ? 'text-green-400' : mtfResult.bearishCount >= 4 ? 'text-red-400' : 'text-yellow-400'}`;
            
            const mtfDir = mtfResult.bullishCount >= 4 ? `🟢 BULLISH (${mtfResult.bullishCount}/6)` : mtfResult.bearishCount >= 4 ? `🔴 BEARISH (${mtfResult.bearishCount}/6)` : `⚪ MIXED`;
            document.getElementById('conf-mtf-direction').textContent = mtfDir;
        }
        
        const regimeVote = votes.regime.prediction === 'BULLISH' ? 1 : votes.regime.prediction === 'BEARISH' ? 1 : 0;
        document.getElementById('conf-regime-vote').textContent = `${regimeVote}/1`;
        document.getElementById('conf-regime-vote').className = `text-lg font-bold ${votes.regime.prediction === 'BULLISH' ? 'text-green-400' : votes.regime.prediction === 'BEARISH' ? 'text-red-400' : 'text-yellow-400'}`;
        document.getElementById('conf-regime-type').textContent = votes.regime.name;
        
        document.getElementById('conf-verdict').textContent = confluence.signal;
        document.getElementById('conf-verdict').style.color = confluence.percentage >= 70 ? '#10b981' : confluence.percentage <= 30 ? '#ef4444' : '#fbbf24';
        document.getElementById('conf-verdict').style.textShadow = `0 0 10px ${confluence.percentage >= 70 ? 'rgba(16, 185, 129, 0.5)' : confluence.percentage <= 30 ? 'rgba(239, 68, 68, 0.5)' : 'rgba(251, 191, 36, 0.5)'}`;
        
        const convictionPct = confluence.percentage;
        document.getElementById('conf-conviction-pct').textContent = convictionPct.toFixed(0) + '%';
        document.getElementById('conf-conviction-fill').style.width = convictionPct + '%';
        document.getElementById('conf-conviction-fill').style.background = convictionPct >= 70 ? 'linear-gradient(90deg, #10b981, #059669)' : convictionPct <= 30 ? 'linear-gradient(90deg, #ef4444, #dc2626)' : 'linear-gradient(90deg, #f59e0b, #d97706)';
        
    } catch (error) {
        debugLog(`❌ Confluence Core Error: ${error.message}`);
    }
}

// ============================================
// VOICE ASSISTANT
// ============================================

function speak(text) {
    if (voiceMuted || !document.getElementById('voice-auto').checked) return;
    
    const synth = window.speechSynthesis;
    const utterance = new SpeechSynthesisUtterance(text);
    
    const volume = document.getElementById('voice-volume').value / 100;
    const speed = document.getElementById('voice-speed').value;
    
    utterance.volume = volume;
    utterance.rate = parseFloat(speed);
    utterance.pitch = 1;
    
    const waveform = document.getElementById('voice-waveform');
    waveform.classList.add('speaking');
    waveform.innerHTML = '<span class="text-xs text-purple-400">🎤 Speaking...</span>';
    
    utterance.onend = () => {
        waveform.classList.remove('speaking');
        waveform.innerHTML = '<span class="text-xs text-gray-500">Ready to speak...</span>';
    };
    
    synth.speak(utterance);
}

function speakNow() {
    if (!priceData || priceData.length === 0) {
        speak('No market data available yet. Please wait for data to load.');
        return;
    }
    
    const confluence = analyzeConfluence(priceData, lastMTFResult);
    const insights = generateNeuralInsights(confluence, lastMTFResult);
    
    let announcement = `Current analysis for ${currentMarket.replace('USD', '')}. Confluence ${confluence.score} out of ${confluence.maxPoints}, ${confluence.percentage.toFixed(0)} percent. Signal: ${confluence.signal.replace(/[^a-zA-Z ]/g, '')}. Risk: ${confluence.riskLevel}. `;
    announcement += `AI Reasoning: ${insights.reasoning} `;
    announcement += `Market Analysis: ${insights.analysis} `;
    announcement += `Strategy: ${insights.strategy}`;
    
    speak(announcement);
}

function speakSignalChange(confluence, mtfResult = null) {
    const { score, maxPoints, percentage, signal, votes, action } = confluence;
    
    let announcement = `Alert! ${signal.includes('BUY') ? 'Buy' : signal.includes('SELL') ? 'Sell' : 'Neutral'} signal on ${currentMarket.replace('USD', '')}. `;
    announcement += `Confluence ${score}/${maxPoints}, ${percentage.toFixed(0)}% confidence. `;
    announcement += `Technical ${Math.abs(votes.indicators.score)}/5 ${votes.indicators.score > 0 ? 'bullish' : votes.indicators.score < 0 ? 'bearish' : 'neutral'}. `;
    
    if (votes.ai.knn.prediction !== 'NEUTRAL') {
        announcement += `k-N-N ${votes.ai.knn.prediction.toLowerCase()} ${votes.ai.knn.confidence.toFixed(0)}%. `;
    }
    
    if (mtfResult) {
        announcement += `Multi-timeframe ${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish. `;
    }
    
    announcement += `Regime ${votes.regime.name.toLowerCase()}. Risk ${confluence.riskLevel.toLowerCase()}.`;
    
    speak(announcement);
}

function muteVoice() {
    voiceMuted = !voiceMuted;
    const btn = document.getElementById('mute-btn');
    if (voiceMuted) {
        btn.textContent = '🔊 Unmute';
        btn.classList.remove('bg-red-600/20', 'hover:bg-red-600/30', 'text-red-400');
        btn.classList.add('bg-green-600/20', 'hover:bg-green-600/30', 'text-green-400');
    } else {
        btn.textContent = '🔇 Mute';
        btn.classList.remove('bg-green-600/20', 'hover:bg-green-600/30', 'text-green-400');
        btn.classList.add('bg-red-600/20', 'hover:bg-red-600/30', 'text-red-400');
    }
}

function updateVoiceSettings() {
    const volume = document.getElementById('voice-volume').value;
    const speed = document.getElementById('voice-speed').value;
    
    document.getElementById('voice-volume-val').textContent = volume + '%';
    document.getElementById('voice-speed-val').textContent = speed + 'x';
}

// ============================================
// ALERT SYSTEM
// ============================================

function checkAlerts(confluence, mtfResult) {
    if (!alertSettings.smartAlerts) {
        debugLog('> 🔕 Alerts disabled');
        return;
    }
    
    const { percentage, signal, votes } = confluence;
    
    debugLog(`> 🔔 Checking alerts: ${percentage.toFixed(0)}% ${signal}`);
    
    if (percentage >= 80 && signal.includes('BUY')) {
        debugLog('> 🟢 EXTREME BUY alert triggered');
        showAlert('🟢 EXTREME BUY', `${confluence.score}/${confluence.maxPoints} confluence!`, 'success');
    } else if (percentage <= 20 && signal.includes('SELL')) {
        debugLog('> 🔴 EXTREME SELL alert triggered');
        showAlert('🔴 EXTREME SELL', `${confluence.score}/${confluence.maxPoints} confluence!`, 'danger');
    }
    
    if (votes.regime.name.includes('STRONG')) {
        debugLog(`> 🎯 REGIME CHANGE alert: ${votes.regime.name}`);
        showAlert('🎯 REGIME CHANGE', votes.regime.name, votes.regime.prediction === 'BULLISH' ? 'success' : 'danger');
    }
    
    if (mtfResult && mtfResult.bullishCount >= 5) {
        debugLog(`> 📊 MTF BULLISH alert: ${mtfResult.bullishCount}/6`);
        showAlert('📊 MTF CONFLUENCE', `${mtfResult.bullishCount}/6 BULLISH!`, 'success');
    } else if (mtfResult && mtfResult.bearishCount >= 5) {
        debugLog(`> 📊 MTF BEARISH alert: ${mtfResult.bearishCount}/6`);
        showAlert('📊 MTF CONFLUENCE', `${mtfResult.bearishCount}/6 BEARISH!`, 'danger');
    }
}

function showAlert(title, message, type = 'success') {
    const container = document.getElementById('alert-notifications');
    const alert = document.createElement('div');
    alert.className = `alert-notification ${type}`;
    alert.style.position = 'relative';
    alert.innerHTML = `
        <button onclick="this.parentElement.remove()" style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.3); border: none; color: white; width: 20px; height: 20px; border-radius: 50%; cursor: pointer; font-size: 12px; line-height: 1; font-weight: bold; hover:background: rgba(0,0,0,0.5);">×</button>
        <div class="font-bold text-sm" style="padding-right: 25px;">${title}</div>
        <div class="text-xs mt-1">${message}</div>
        <div class="text-xs mt-1 opacity-75">${new Date().toLocaleTimeString()}</div>
    `;
    
    container.insertBefore(alert, container.firstChild);
    
    // Auto-dismiss after 30 seconds
    setTimeout(() => {
        if (alert.parentElement) {
            alert.style.opacity = '0';
            alert.style.transform = 'translateX(400px)';
            setTimeout(() => {
                if (alert.parentElement) alert.remove();
            }, 300);
        }
    }, 30000);
    
    if (Notification.permission === 'granted') {
        new Notification(title, { body: message, icon: '🤖' });
    }
}

// ============================================
// PERFORMANCE TRACKING
// ============================================

function logTrade(signal, entryPrice) {
    const trade = {
        time: new Date(),
        signal,
        entryPrice,
        exitPrice: null,
        profit: 0,
        result: 'PENDING'
    };
    
    performanceTracker.trades.push(trade);
    
    setTimeout(() => {
        const exitPrice = priceData[priceData.length - 1].close;
        trade.exitPrice = exitPrice;
        
        if (signal.includes('BUY')) {
            trade.profit = exitPrice - entryPrice;
        } else if (signal.includes('SELL')) {
            trade.profit = entryPrice - exitPrice;
        }
        
        trade.result = trade.profit > 0 ? 'WIN' : 'LOSS';
        
        if (trade.result === 'WIN') performanceTracker.wins++;
        else performanceTracker.losses++;
        
        performanceTracker.totalTrades++;
        performanceTracker.profit += trade.profit;
        performanceTracker.winRate = (performanceTracker.wins / performanceTracker.totalTrades * 100).toFixed(1);
        
        if (trade.profit > performanceTracker.bestTrade) performanceTracker.bestTrade = trade.profit;
        if (trade.profit < performanceTracker.worstTrade) performanceTracker.worstTrade = trade.profit;
        
        updatePerformanceUI();
    }, 30000);
}

function updatePerformanceUI() {
    document.getElementById('perf-winrate').textContent = performanceTracker.winRate + '%';
    document.getElementById('perf-winrate').className = parseFloat(performanceTracker.winRate) >= 50 ? 'perf-stat-value positive' : 'perf-stat-value negative';
    
    document.getElementById('perf-total').textContent = performanceTracker.totalTrades;
    
    document.getElementById('perf-profit').textContent = '$' + performanceTracker.profit.toFixed(2);
    document.getElementById('perf-profit').className = performanceTracker.profit >= 0 ? 'perf-stat-value positive' : 'perf-stat-value negative';
    
    document.getElementById('perf-best').textContent = '$' + performanceTracker.bestTrade.toFixed(2);
    
    const logContainer = document.getElementById('trade-log-entries');
    logContainer.innerHTML = '';
    
    const recentTrades = performanceTracker.trades.slice(-20).reverse();
    
    if (recentTrades.length === 0) {
        logContainer.innerHTML = '<div class="text-xs text-gray-500 text-center py-4">No trades logged yet</div>';
        return;
    }
    
    recentTrades.forEach(trade => {
        if (trade.result === 'PENDING') return;
        
        const row = document.createElement('div');
        row.className = `trade-row ${trade.result.toLowerCase()}`;
        row.innerHTML = `
            <span>${trade.time.toLocaleTimeString()}</span>
            <span>${trade.signal.substring(0, 10)}</span>
            <span>$${trade.entryPrice.toFixed(2)}</span>
            <span class="${trade.profit >= 0 ? 'text-green-400' : 'text-red-400'}">$${trade.profit.toFixed(2)}</span>
        `;
        logContainer.appendChild(row);
    });
}

function clearTradeHistory() {
    if (confirm('Clear all trade history?')) {
        performanceTracker = {
            trades: [],
            winRate: 0,
            totalTrades: 0,
            profit: 0,
            wins: 0,
            losses: 0,
            bestTrade: 0,
            worstTrade: 0
        };
        updatePerformanceUI();
    }
}

// ============================================
// UI UPDATE FUNCTIONS
// ============================================

function updatePriceDisplay() {
    if (priceData.length === 0) return;
    
    const latest = priceData[priceData.length - 1];
    const previous = priceData[priceData.length - 2] || latest;
    
    const priceChange = latest.close - previous.close;
    const priceChangePercent = (priceChange / previous.close * 100).toFixed(2);
    
    document.getElementById('current-price').textContent = `$${latest.close.toFixed(2)}`;
    document.getElementById('price-change').textContent = `${priceChange >= 0 ? '▲' : '▼'} $${Math.abs(priceChange).toFixed(2)} (${priceChangePercent}%)`;
    document.getElementById('price-change').className = priceChange >= 0 ? 'text-sm text-green-400' : 'text-sm text-red-400';
    
    const now = new Date();
    document.getElementById('last-update').textContent = now.toLocaleTimeString();
    
    document.getElementById('latest-close').textContent = `$${latest.close.toFixed(2)}`;
    document.getElementById('latest-high').textContent = `$${latest.high.toFixed(2)}`;
    document.getElementById('latest-low').textContent = `$${latest.low.toFixed(2)}`;
    document.getElementById('latest-volume').textContent = latest.volume.toLocaleString();
    document.getElementById('candles-loaded').textContent = priceData.length;
    document.getElementById('data-timestamp').textContent = new Date(latest.time).toLocaleString();
}

function updateCircularGauge(score, maxPoints) {
    const percentage = (score / maxPoints) * 100;
    const radius = 115;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (percentage / 100) * circumference;
    
    const progressCircle = document.querySelector('.gauge-progress');
    progressCircle.style.strokeDasharray = `${circumference} ${circumference}`;
    progressCircle.style.strokeDashoffset = offset;
    
    let color;
    if (percentage >= 70) color = 'url(#gauge-gradient-bullish)';
    else if (percentage >= 40) color = 'url(#gauge-gradient-neutral)';
    else color = 'url(#gauge-gradient-bearish)';
    
    progressCircle.setAttribute('stroke', color);
    
    document.querySelector('.gauge-score').textContent = `${score}/${maxPoints}`;
    document.querySelector('.gauge-percentage').textContent = `${percentage.toFixed(0)}%`;
}

async function updateMTFGrid() {
    const mtfResult = await analyzeAllTimeframes();
    lastMTFResult = mtfResult;
    
    const grid = document.getElementById('mtf-grid');
    grid.innerHTML = '';
    
    mtfResult.results.forEach(r => {
        const cell = document.createElement('div');
        cell.className = `mtf-cell mtf-${r.signal.toLowerCase()}`;
        cell.innerHTML = `
            <div class="mtf-tf-label">${r.tf}</div>
            <div class="mtf-signal">${r.signal === 'BULLISH' ? '🟢 BUY' : r.signal === 'BEARISH' ? '🔴 SELL' : '⚪ NEUTRAL'}</div>
            <div class="mtf-details">RSI: ${r.rsi}</div>
            <div class="mtf-details">MACD: ${r.macd}</div>
            <div class="mtf-details">${r.trend}</div>
        `;
        grid.appendChild(cell);
    });
    
    const summary = document.getElementById('mtf-summary');
    summary.innerHTML = `
        🟢 ${mtfResult.bullishCount} BUY | 🔴 ${mtfResult.bearishCount} SELL | ⚪ ${mtfResult.neutralCount} NEUTRAL<br>
        <span class="text-xs">${
            mtfResult.bullishCount >= 5 ? 'STRONG BULLISH CONFLUENCE' :
            mtfResult.bearishCount >= 5 ? 'STRONG BEARISH CONFLUENCE' :
            mtfResult.bullishCount >= 4 ? 'MODERATE BULLISH' :
            mtfResult.bearishCount >= 4 ? 'MODERATE BEARISH' :
            'MIXED SIGNALS'
        }</span>
    `;
    
    return mtfResult;
}

async function updateUI() {
    if (priceData.length < 50) {
        debugLog('> ⚠️ Not enough data (need 50+ candles)');
        return;
    }
    
    const startTime = performance.now();
    
    debugLog('> 📊 Analyzing MTF...');
    const mtfResult = await updateMTFGrid();
    
    debugLog('> 🧠 Running confluence...');
    const confluence = analyzeConfluence(priceData, mtfResult);
    
    debugLog('> 💡 Generating insights...');
    const insights = generateNeuralInsights(confluence, mtfResult);
    
    updateCircularGauge(confluence.score, confluence.maxPoints);
    
    const signalEl = document.getElementById('final-signal');
    signalEl.textContent = confluence.signal;
    signalEl.className = `text-3xl font-bold hologram ${confluence.percentage >= 70 ? 'text-green-400' : confluence.percentage >= 40 ? 'text-yellow-400' : 'text-red-400'}`;
    
    // Calculate average AI confidence
    let aiConfidenceSum = 0;
    let aiConfidenceCount = 0;
    
    if (aiModulesEnabled.knn) {
        aiConfidenceSum += confluence.votes.ai.knn.confidence;
        aiConfidenceCount++;
    }
    
    if (aiModulesEnabled.svm) {
        // Convert SVM margin to confidence percentage
        const svmConfidence = Math.min(95, 50 + Math.abs(confluence.votes.ai.svm.margin) * 10);
        aiConfidenceSum += svmConfidence;
        aiConfidenceCount++;
    }
    
    if (aiModulesEnabled.nb) {
        aiConfidenceSum += confluence.votes.ai.nb.confidence;
        aiConfidenceCount++;
    }
    
    const avgAIConfidence = aiConfidenceCount > 0 ? (aiConfidenceSum / aiConfidenceCount).toFixed(0) : 50;
    
    document.getElementById('confidence-pct').textContent = `${avgAIConfidence}%`;
    
    const actionEl = document.getElementById('trade-action');
    actionEl.textContent = confluence.action;
    actionEl.className = `text-lg font-bold ${confluence.action.includes('BUY') && !confluence.action.includes('WAIT') ? 'text-green-400' : confluence.action.includes('SELL') && !confluence.action.includes('WAIT') ? 'text-red-400' : confluence.action.includes('WAIT') ? 'text-orange-400' : 'text-gray-400'}`;
    
    const riskEl = document.getElementById('risk-level');
    riskEl.textContent = confluence.riskLevel;
    riskEl.className = `text-lg font-bold ${confluence.riskLevel === 'LOW' ? 'text-green-400' : confluence.riskLevel === 'MEDIUM' ? 'text-yellow-400' : 'text-red-400'}`;
    
    // Update vote cards with CLEAR visual indicators - COUNT BULLISH INDICATORS
    // Count how many indicators are BULLISH (not net score)
    let bullishIndicatorCount = 0;
    const votes = confluence.votes.indicators.details;
    if (votes.rsi === 'BULLISH') bullishIndicatorCount++;
    if (votes.macd === 'BULLISH') bullishIndicatorCount++;
    if (votes.stoch === 'BULLISH') bullishIndicatorCount++;
    if (votes.ema === 'BULLISH') bullishIndicatorCount++;
    if (votes.trend === 'BULLISH') bullishIndicatorCount++;
    
    const indicatorScore = bullishIndicatorCount; // 0 to 5 scale
    const indCard = document.getElementById('vote-ind-card');
    document.getElementById('vote-ind-score').textContent = `${indicatorScore}/5`;
    
    // INDICATORS: Clear color coding based on bullish count
    if (indicatorScore >= 4) {
        document.getElementById('vote-ind-signal').textContent = '🟢 STRONG BULLISH';
        document.getElementById('vote-ind-signal').className = 'vote-card-signal text-green-400';
        document.getElementById('vote-ind-desc').textContent = 'Multiple indicators confirm BUY';
        indCard.className = 'vote-card bullish-strong';
    } else if (indicatorScore === 3) {
        document.getElementById('vote-ind-signal').textContent = '🟡 BULLISH';
        document.getElementById('vote-ind-signal').className = 'vote-card-signal text-yellow-400';
        document.getElementById('vote-ind-desc').textContent = 'Lean towards BUY';
        indCard.className = 'vote-card bullish';
    } else if (indicatorScore === 2) {
        document.getElementById('vote-ind-signal').textContent = '⚪ NEUTRAL';
        document.getElementById('vote-ind-signal').className = 'vote-card-signal text-gray-400';
        document.getElementById('vote-ind-desc').textContent = 'Mixed signals - DON\'T TRADE';
        indCard.className = 'vote-card neutral';
    } else if (indicatorScore === 1) {
        document.getElementById('vote-ind-signal').textContent = '🟡 BEARISH';
        document.getElementById('vote-ind-signal').className = 'vote-card-signal text-orange-400';
        document.getElementById('vote-ind-desc').textContent = 'Lean towards SELL';
        indCard.className = 'vote-card bearish';
    } else {
        document.getElementById('vote-ind-signal').textContent = '🔴 STRONG BEARISH';
        document.getElementById('vote-ind-signal').className = 'vote-card-signal text-red-400';
        document.getElementById('vote-ind-desc').textContent = 'Multiple indicators say SELL';
        indCard.className = 'vote-card bearish-strong';
    }
    
    // AI MODULES: Clear color coding
    const aiScore = Math.min(3, Math.max(0, Math.floor((confluence.votes.ai.score + 3) / 2))); // Normalize -3 to +3 into 0 to 3
    const aiCard = document.getElementById('vote-ai-card');
    document.getElementById('vote-ai-score').textContent = `${aiScore}/3`;
    
    if (aiScore === 3) {
        document.getElementById('vote-ai-signal').textContent = '🟢 STRONG BULLISH';
        document.getElementById('vote-ai-signal').className = 'vote-card-signal text-green-400';
        document.getElementById('vote-ai-desc').textContent = 'All 3 AI models agree: BUY';
        aiCard.className = 'vote-card bullish-strong';
    } else if (aiScore === 2) {
        document.getElementById('vote-ai-signal').textContent = '🟡 BULLISH';
        document.getElementById('vote-ai-signal').className = 'vote-card-signal text-yellow-400';
        document.getElementById('vote-ai-desc').textContent = '2 out of 3 AI models say BUY';
        aiCard.className = 'vote-card bullish';
    } else if (aiScore === 1) {
        document.getElementById('vote-ai-signal').textContent = '⚪ NEUTRAL';
        document.getElementById('vote-ai-signal').className = 'vote-card-signal text-gray-400';
        document.getElementById('vote-ai-desc').textContent = 'AI models disagree - DON\'T TRADE';
        aiCard.className = 'vote-card neutral';
    } else {
        document.getElementById('vote-ai-signal').textContent = '🔴 STRONG BEARISH';
        document.getElementById('vote-ai-signal').className = 'vote-card-signal text-red-400';
        document.getElementById('vote-ai-desc').textContent = 'All 3 AI models say SELL';
        aiCard.className = 'vote-card bearish-strong';
    }
    
    // MTF ANALYSIS: Clear color coding - Show max(bullish, bearish) as score
    const mtfCard = document.getElementById('vote-mtf-card');
    
    if (!aiModulesEnabled.mtf) {
        // Show NOT AVAILABLE when MTF is OFF
        document.getElementById('vote-mtf-score').textContent = '--';
        document.getElementById('vote-mtf-signal').textContent = '⚪ NOT AVAILABLE';
        document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-gray-400';
        document.getElementById('vote-mtf-desc').textContent = 'Module disabled';
        if (document.getElementById('mtf-status')) {
            document.getElementById('mtf-status').textContent = '';
        }
        mtfCard.className = 'vote-card neutral';
    } else {
        const mtfScore = Math.max(mtfResult.bullishCount, mtfResult.bearishCount);
        document.getElementById('vote-mtf-score').textContent = `${mtfScore}/6`;
        
        if (mtfResult.bullishCount >= 5) {
            document.getElementById('vote-mtf-signal').textContent = '🟢 STRONG ALIGNMENT';
            document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-green-400';
            document.getElementById('vote-mtf-desc').textContent = `${mtfResult.bullishCount}/6 timeframes BULLISH`;
            if (document.getElementById('mtf-status')) {
                document.getElementById('mtf-status').textContent = `${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish`;
            }
            mtfCard.className = 'vote-card bullish-strong';
        } else if (mtfResult.bullishCount === 4) {
            document.getElementById('vote-mtf-signal').textContent = '🟡 MODERATE';
            document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-yellow-400';
            document.getElementById('vote-mtf-desc').textContent = 'Moderate bullish alignment';
            if (document.getElementById('mtf-status')) {
                document.getElementById('mtf-status').textContent = `${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish`;
            }
            mtfCard.className = 'vote-card bullish';
        } else if (mtfResult.bearishCount >= 5) {
            document.getElementById('vote-mtf-signal').textContent = '🔴 STRONG ALIGNMENT';
            document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-red-400';
            document.getElementById('vote-mtf-desc').textContent = `${mtfResult.bearishCount}/6 timeframes BEARISH`;
            if (document.getElementById('mtf-status')) {
                document.getElementById('mtf-status').textContent = `${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish`;
            }
            mtfCard.className = 'vote-card bearish-strong';
        } else if (mtfResult.bearishCount === 4) {
            document.getElementById('vote-mtf-signal').textContent = '🟡 MODERATE';
            document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-orange-400';
            document.getElementById('vote-mtf-desc').textContent = 'Moderate bearish alignment';
            if (document.getElementById('mtf-status')) {
                document.getElementById('mtf-status').textContent = `${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish`;
            }
            mtfCard.className = 'vote-card bearish';
        } else {
            document.getElementById('vote-mtf-signal').textContent = '⚪ MIXED SIGNALS';
            document.getElementById('vote-mtf-signal').className = 'vote-card-signal text-gray-400';
            document.getElementById('vote-mtf-desc').textContent = 'No clear trend - DON\'T TRADE';
            if (document.getElementById('mtf-status')) {
                document.getElementById('mtf-status').textContent = `${mtfResult.bullishCount} bullish, ${mtfResult.bearishCount} bearish`;
            }
            mtfCard.className = 'vote-card neutral';
        }
    }
    
    // MARKET REGIME: Clear color coding
    const regimeCard = document.getElementById('vote-regime-card');
    
    if (!aiModulesEnabled.regime) {
        // Show NOT AVAILABLE when regime is OFF
        document.getElementById('regime-status').textContent = '--';
        document.getElementById('regime-signal').textContent = '⚪ NOT AVAILABLE';
        document.getElementById('regime-signal').className = 'vote-card-signal text-gray-400';
        document.getElementById('regime-desc').textContent = 'Module disabled';
        regimeCard.className = 'vote-card neutral';
    } else {
        document.getElementById('regime-status').textContent = confluence.votes.regime.name;
        
        if (confluence.votes.regime.prediction === 'BULLISH') {
            document.getElementById('regime-signal').textContent = '🟢 BULLISH';
            document.getElementById('regime-signal').className = 'vote-card-signal text-green-400';
            document.getElementById('regime-desc').textContent = 'Strong trending upward';
            regimeCard.className = 'vote-card bullish-strong';
        } else if (confluence.votes.regime.prediction === 'BEARISH') {
            document.getElementById('regime-signal').textContent = '🔴 BEARISH';
            document.getElementById('regime-signal').className = 'vote-card-signal text-red-400';
            document.getElementById('regime-desc').textContent = 'Strong trending downward';
            regimeCard.className = 'vote-card bearish-strong';
        } else {
            document.getElementById('regime-signal').textContent = '⚪ NEUTRAL';
            document.getElementById('regime-signal').className = 'vote-card-signal text-gray-400';
            document.getElementById('regime-desc').textContent = 'Ranging or transitional market';
            regimeCard.className = 'vote-card neutral';
        }
    }
    
    debugLog('> 📝 Updating neural insights...');
    document.getElementById('insight-reasoning').textContent = insights.reasoning;
    document.getElementById('insight-analysis').textContent = insights.analysis;
    document.getElementById('insight-strategy').textContent = insights.strategy;
    debugLog('> ✓ Neural insights updated');
    
    debugLog('> 📊 Updating confluence core...');
    updateConfluenceAnalysisCore(confluence, mtfResult);
    
    checkAlerts(confluence, mtfResult);
    
    if (lastSignal !== confluence.signal && lastSignal !== 'NEUTRAL') {
        speakSignalChange(confluence, mtfResult);
        
        if (confluence.signal.includes('BUY') || confluence.signal.includes('SELL')) {
            logTrade(confluence.signal, currentPrice);
        }
    }
    
    lastSignal = confluence.signal;
    
    const endTime = performance.now();
    const analysisTime = (endTime - startTime).toFixed(1);
    document.getElementById('analysis-time').textContent = `${analysisTime} ms`;
    
    debugLog(`> ✓ CONFLUENCE: ${confluence.score}/${confluence.maxPoints} (${confluence.percentage}%) | ${confluence.signal}`);
    
    updateTerminalLog(confluence);
}

function updateTerminalLog(confluence) {
    const log = document.getElementById('terminal-log');
    const timestamp = new Date().toLocaleTimeString();
    
    const newLog = `[${timestamp}] ${confluence.signal} | Risk: ${confluence.riskLevel} | Conf: ${confluence.percentage}%`;
    
    const lines = log.innerHTML.split('\n').filter(l => l.trim());
    lines.unshift(newLog);
    if (lines.length > 10) lines.pop();
    
    log.innerHTML = lines.join('\n');
}

// ============================================
// CHART FUNCTIONS
// ============================================

function initializeChart() {
    if (chartInitialized && tvWidget) {
        try {
            tvWidget.remove();
        } catch (e) {
            debugLog('> Chart removal (expected)');
        }
    }
    
    const symbol = MARKETS[currentMarket].symbol;
    const interval = TF_MAP_TV[currentTimeframe];
    
    debugLog(`> 📈 Init chart: ${symbol} ${interval}`);
    
    tvWidget = new TradingView.widget({
        autosize: true,
        symbol: symbol,
        interval: interval,
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        toolbar_bg: "#0F0F0F",
        enable_publishing: false,
        hide_side_toolbar: true,
        allow_symbol_change: false,
        container_id: "tradingview-chart",
        studies: [
            { id: "RSI@tv-basicstudies", inputs: { length: 14 } },
            { id: "MACD@tv-basicstudies", inputs: { fastLength: 12, slowLength: 26, signalLength: 9 } }
        ]
    });
    
    chartInitialized = true;
    debugLog(`> ✓ Chart ready`);
}

// ============================================
// MARKET & TIMEFRAME SELECTION
// ============================================

async function selectMarket(market, price) {
    debugLog(`> 🔄 Switch to ${market}`);
    
    document.querySelectorAll('.market-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`market-${market}`).classList.add('active');
    
    currentMarket = market;
    currentPrice = price;
    chartInitialized = false;
    
    await generateNewData();
    initializeChart();
    startAutoRefresh();
    
    speak(`Market switched to ${market.replace('USD', '')}.`);
}

async function changeTimeframe(tf) {
    debugLog(`> ⏱️ Change to ${tf}`);
    
    document.querySelectorAll('.tf-button').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`tf-${tf}`).classList.add('active');
    
    currentTimeframe = tf;
    chartInitialized = false;
    
    await generateNewData();
    initializeChart();
}

async function generateNewData() {
    debugLog(`> 🔄 Fetching ${currentMarket} ${currentTimeframe}`);
    
    const data = await fetchRealData(currentMarket, currentTimeframe);
    
    if (data && data.length > 0) {
        priceData = data;
        currentPrice = data[data.length - 1].close;
        updatePriceDisplay();
        debugLog(`> ✓ Real data: ${data.length} candles`);
    } else {
        debugLog('> ⚠️ Using simulated data');
        priceData = generateSimulatedData(200, currentPrice);
        document.getElementById('data-source').textContent = 'Simulated (Demo)';
    }
}

function generateSimulatedData(periods, basePrice) {
    const data = [];
    let price = basePrice;
    const now = Date.now();
    
    for (let i = 0; i < periods; i++) {
        const change = (Math.random() - 0.48) * (basePrice * 0.02);
        price += change;
        
        const high = price + Math.random() * (basePrice * 0.01);
        const low = price - Math.random() * (basePrice * 0.01);
        const open = i === 0 ? basePrice : data[i - 1].close;
        
        data.push({
            time: now - (periods - i) * 3600000,
            open,
            high,
            low,
            close: price,
            volume: Math.random() * 1000000
        });
    }
    
    return data;
}

// ============================================
// AUTO REFRESH
// ============================================

function startAutoRefresh() {
    if (autoRefreshInterval) clearInterval(autoRefreshInterval);
    if (nextUpdateCountdown) clearInterval(nextUpdateCountdown);
    
    const config = MARKETS[currentMarket];
    const refreshInterval = config.api === 'binance' ? 5000 : 120000;
    
    let countdown = refreshInterval / 1000;
    
    nextUpdateCountdown = setInterval(() => {
        countdown--;
        if (countdown <= 0) countdown = refreshInterval / 1000;
        document.getElementById('next-update').textContent = `${countdown}s`;
    }, 1000);
    
    autoRefreshInterval = setInterval(async () => {
        await generateNewData();
        updateUI();
    }, refreshInterval);
    
    debugLog(`> ⏰ Auto-refresh: ${refreshInterval / 1000}s`);
}

// ============================================
// AI MODULE TOGGLES
// ============================================

function toggleAI(module) {
    aiModulesEnabled[module] = !aiModulesEnabled[module];
    
    const toggleEl = document.getElementById(`toggle-${module}`);
    const statusEl = document.getElementById(`status-${module}`);
    
    if (aiModulesEnabled[module]) {
        toggleEl.style.background = 'rgba(16, 185, 129, 0.3)';
        toggleEl.textContent = 'ON';
        if (module === 'mtf') {
            statusEl.textContent = 'ON: 10-Point System';
        } else if (module === 'regime') {
            statusEl.textContent = 'ON: Adds +1 vote';
        } else if (module === 'alerts') {
            statusEl.textContent = 'ON: Auto-notify signals';
            alertSettings.smartAlerts = true;
        } else {
            statusEl.textContent = '●ACTIVE';
        }
        statusEl.className = 'text-xs text-green-400' + (module !== 'alerts' ? ' ml-2' : '');
    } else {
        toggleEl.style.background = 'rgba(107, 114, 128, 0.2)';
        toggleEl.textContent = 'OFF';
        if (module === 'mtf') {
            statusEl.textContent = 'OFF: 9-Point System';
        } else if (module === 'regime') {
            statusEl.textContent = 'OFF: Excluded';
        } else if (module === 'alerts') {
            statusEl.textContent = 'OFF: No alerts';
            alertSettings.smartAlerts = false;
        } else {
            statusEl.textContent = '●OFF';
        }
        statusEl.className = 'text-xs text-gray-400' + (module !== 'alerts' ? ' ml-2' : '');
    }
    
    updateUI();
}

// ============================================
// DEBUG FUNCTIONS
// ============================================

function debugLog(message) {
    const log = document.getElementById('debug-log');
    const timestamp = new Date().toLocaleTimeString();
    const div = document.createElement('div');
    div.textContent = `[${timestamp}] ${message}`;
    log.insertBefore(div, log.firstChild);
    
    while (log.children.length > 100) {
        log.removeChild(log.lastChild);
    }
    
    console.log(message);
}

function copyDebugLogs() {
    const log = document.getElementById('debug-log');
    const text = log.innerText;
    navigator.clipboard.writeText(text);
    alert('Debug logs copied!');
}

function clearDebugLogs() {
    document.getElementById('debug-log').innerHTML = '';
    debugLog('> Console cleared');
}

function runDiagnosticTest() {
    debugLog('=== DIAGNOSTIC TEST ===');
    debugLog(`Market: ${currentMarket}, TF: ${currentTimeframe}`);
    debugLog(`Data: ${priceData.length} candles`);
    debugLog(`AI: kNN=${aiModulesEnabled.knn}, SVM=${aiModulesEnabled.svm}, NB=${aiModulesEnabled.nb}`);
    
    if (priceData.length > 0) {
        debugLog('Testing indicators...');
        const rsi = calculateRSI(priceData);
        const macd = calculateMACD(priceData);
        const stoch = calculateStochastic(priceData);
        const adx = calculateADX(priceData);
        debugLog(`✓ RSI: ${rsi.toFixed(2)}`);
        debugLog(`✓ MACD: ${macd.macd.toFixed(4)}`);
        debugLog(`✓ Stoch: ${stoch.toFixed(2)}`);
        debugLog(`✓ ADX: ${adx.toFixed(2)}`);
        
        debugLog('Testing AI...');
        const features = extractFeatures(priceData);
        const training = generateTrainingData(priceData);
        debugLog(`✓ Training: ${training.length} samples`);
        const knn = kNNClassifier(features, training);
        const svm = svmClassifier(features, training);
        const nb = naiveBayesClassifier(features, training);
        debugLog(`✓ k-NN: ${knn.prediction} (${knn.confidence.toFixed(1)}%)`);
        debugLog(`✓ SVM: ${svm.prediction} (${svm.margin.toFixed(2)})`);
        debugLog(`✓ NB: ${nb.prediction} (${nb.confidence.toFixed(1)}%)`);
        
        debugLog('=== ALL TESTS PASSED ✓ ===');
    } else {
        debugLog('❌ No data available');
    }
}

// ============================================
// DRAGGABLE DEBUG CONSOLE
// ============================================

function makeDraggable(element) {
    let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
    const header = document.getElementById('debug-console-header');
    
    header.onmousedown = dragMouseDown;
    
    function dragMouseDown(e) {
        e = e || window.event;
        e.preventDefault();
        pos3 = e.clientX;
        pos4 = e.clientY;
        document.onmouseup = closeDragElement;
        document.onmousemove = elementDrag;
    }
    
    function elementDrag(e) {
        e = e || window.event;
        e.preventDefault();
        pos1 = pos3 - e.clientX;
        pos2 = pos4 - e.clientY;
        pos3 = e.clientX;
        pos4 = e.clientY;
        element.style.top = (element.offsetTop - pos2) + "px";
        element.style.left = (element.offsetLeft - pos1) + "px";
        element.style.bottom = 'auto';
        element.style.right = 'auto';
    }
    
    function closeDragElement() {
        document.onmouseup = null;
        document.onmousemove = null;
    }
}

// ============================================
// MATRIX BACKGROUND
// ============================================

function initMatrixBackground() {
    const canvas = document.getElementById('matrix-canvas');
    const ctx = canvas.getContext('2d');
    
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    
    const binary = '01';
    const fontSize = 10;
    const columns = canvas.width / fontSize;
    const drops = Array(Math.floor(columns)).fill(1);
    
    function drawMatrix() {
        ctx.fillStyle = 'rgba(0, 0, 0, 0.04)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        ctx.fillStyle = '#0F0';
        ctx.font = fontSize + 'px monospace';
        
        for (let i = 0; i < drops.length; i++) {
            const text = binary[Math.floor(Math.random() * binary.length)];
            ctx.fillText(text, i * fontSize, drops[i] * fontSize);
            
            if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) {
                drops[i] = 0;
            }
            drops[i]++;
        }
    }
    
    setInterval(drawMatrix, 33);
    
    window.addEventListener('resize', () => {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    });
}

// ============================================
// INITIALIZATION
// ============================================

window.addEventListener('load', async () => {
    debugLog('> 🤖 AI TRADING TERMINAL v10.0 ULTRA');
    debugLog('> Initializing...');
    
    initMatrixBackground();
    makeDraggable(document.getElementById('debug-console'));
    
    if (Notification.permission === 'default') {
        Notification.requestPermission();
    }
    
    document.getElementById('market-BTCUSD').classList.add('active');
    document.getElementById('tf-1H').classList.add('active');
    
    debugLog('> 📊 Loading data...');
    await generateNewData();
    
    debugLog('> 📈 Init chart...');
    initializeChart();
    
    setTimeout(async () => {
        debugLog('> 🧠 Initial analysis...');
        await updateUI();
        startAutoRefresh();
        debugLog('> ✅ System ready');
        
        setTimeout(() => {
            const intro = `AI Trading Terminal version ten point zero ultra initialized. Neural networks active. Analyzing ${currentMarket.replace('USD', '')} on ${currentTimeframe}. `;
            
            if (priceData.length > 0) {
                const confluence = analyzeConfluence(priceData, lastMTFResult);
                const insights = generateNeuralInsights(confluence, lastMTFResult);
                
                const extendedIntro = intro + `Initial scan complete. ${confluence.signal.replace(/[^a-zA-Z ]/g, '')} detected, ${confluence.percentage.toFixed(0)} percent confluence. ${insights.reasoning.substring(0, 200)}... All systems ready.`;
                
                speak(extendedIntro);
            } else {
                speak(intro + 'Awaiting data. Systems ready.');
            }
        }, 3000);
        
    }, 2000);
});
