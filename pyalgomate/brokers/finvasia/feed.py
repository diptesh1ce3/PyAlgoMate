"""
.. moduleauthor:: Nagaraju Gunda
"""

import datetime
import logging
import six

from pyalgotrade import bar
from pyalgotrade import barfeed
from pyalgotrade import observer
from pyalgomate.brokers.finvasia import wsclient

logger = logging.getLogger(__name__)


class TradeBar(bar.Bar):
    def __init__(self, trade):
        self.__dateTime = trade.getDateTime()
        self.__trade = trade

    def getInstrument(self):
        return self.__trade.getExtraColumns().get("instrument")

    def setUseAdjustedValue(self, useAdjusted):
        if useAdjusted:
            raise Exception("Adjusted close is not available")

    def getTrade(self):
        return self.__trade

    def getTradeId(self):
        return self.__trade.getId()

    def getFrequency(self):
        return bar.Frequency.TRADE

    def getDateTime(self):
        return self.__dateTime

    def getOpen(self, adjusted=False):
        return self.__trade.getPrice()

    def getHigh(self, adjusted=False):
        return self.__trade.getPrice()

    def getLow(self, adjusted=False):
        return self.__trade.getPrice()

    def getClose(self, adjusted=False):
        return self.__trade.getPrice()

    def getVolume(self):
        return self.__trade.getAmount()

    def getAdjClose(self):
        return None

    def getTypicalPrice(self):
        return self.__trade.getPrice()

    def getPrice(self):
        return self.__trade.getPrice()

    def getUseAdjValue(self):
        return False

    def isBuy(self):
        return self.__trade.isBuy()

    def isSell(self):
        return not self.__trade.isBuy()


class LiveTradeFeed(barfeed.BaseBarFeed):

    """A real-time BarFeed that builds bars from live trades.

    :param instruments: A list of currency pairs.
    :type instruments: list of :class:`pyalgotrade.instrument.Instrument` or a string formatted like
        QUOTE_SYMBOL/PRICE_CURRENCY..
    :param maxLen: The maximum number of values that the :class:`pyalgotrade.dataseries.bards.BarDataSeries` will hold.
        Once a bounded length is full, when new items are added, a corresponding number of items are discarded
        from the opposite end. If None then dataseries.DEFAULT_MAX_LEN is used.
    :type maxLen: int.

    .. note::
        Note that a Bar will be created for every trade, so open, high, low and close values will all be the same.
    """

    QUEUE_TIMEOUT = 0.01

    def __init__(self, api, instruments, timeout=10, maxLen=None):
        super(LiveTradeFeed, self).__init__(bar.Frequency.TRADE, maxLen)
        self.__tradeBars = {}
        self.__channels = []
        self.__api = api
        self.__timeout = timeout

        for instrument in instruments:
            self.__channels.append(instrument)
            self.registerDataSeries(instrument)

        self.__thread = None
        self.__enableReconnection = True
        self.__stopped = False
        self.__orderBookUpdateEvent = observer.Event()

    # Factory method for testing purposes.
    def buildWebSocketClientThread(self):
        return wsclient.WebSocketClientThread(self.__api, self.__channels)

    def getCurrentDateTime(self):
        return datetime.datetime.now()

    def enableReconection(self, enableReconnection):
        self.__enableReconnection = enableReconnection

    def __initializeClient(self):
        logger.info("Initializing websocket client")
        initialized = False
        try:
            # Start the thread that runs the client.
            self.__thread = self.buildWebSocketClientThread()
            self.__thread.start()
        except Exception as e:
            logger.error("Error connecting : %s" % str(e))

        logger.info("Waiting for websocket initialization to complete")
        while not initialized and not self.__stopped:
            initialized = self.__thread.waitInitialized(self.__timeout)

        if initialized:
            logger.info("Initialization completed")
        else:
            logger.error("Initialization failed")
        return initialized

    def __onDisconnected(self):
        if self.__enableReconnection:
            logger.info("Reconnecting")
            while not self.__stopped and not self.__initializeClient():
                pass
        elif not self.__stopped:
            logger.info("Stopping")
            self.__stopped = True

    def __dispatchImpl(self, eventFilter):
        ret = False
        try:
            eventType, eventData = self.__thread.getQueue().get(
                True, LiveTradeFeed.QUEUE_TIMEOUT)
            if eventFilter is not None and eventType not in eventFilter:
                return False

            ret = True
            if eventType == wsclient.WebSocketClient.Event.TRADE:
                self.__onTrade(eventData)
            elif eventType == wsclient.WebSocketClient.Event.ORDER_BOOK_UPDATE:
                self.__orderBookUpdateEvent.emit(eventData)
            elif eventType == wsclient.WebSocketClient.Event.DISCONNECTED:
                self.__onDisconnected()
            else:
                ret = False
                logger.error(
                    "Invalid event received to dispatch: %s - %s" % (eventType, eventData))
        except six.moves.queue.Empty:
            pass
        return ret

    def __onTrade(self, trade):
        dateTime = trade.getDateTime()
        if dateTime not in self.__tradeBars:
            self.__tradeBars[dateTime] = []

        self.__tradeBars[trade.getDateTime()].append(
            {trade.getExtraColumns().get("instrument"): trade})

    def barsHaveAdjClose(self):
        return False

    def getNextBars(self):
        ret = None
        if len(self.__tradeBars):
            self.__tradeBars = dict(sorted(self.__tradeBars.items()))
            dateTime = next(iter(self.__tradeBars))
            tradeBar = self.__tradeBars[dateTime].pop(0)
            if (len(self.__tradeBars[dateTime]) == 0):
                self.__tradeBars.pop(dateTime)

            ret = bar.Bars(tradeBar)
        return ret

    def peekDateTime(self):
        # Return None since this is a realtime subject.
        return None

    # This may raise.
    def start(self):
        super(LiveTradeFeed, self).start()
        if self.__thread is not None:
            raise Exception("Already running")
        elif not self.__initializeClient():
            self.__stopped = True
            raise Exception("Initialization failed")

    def dispatch(self):
        # Note that we may return True even if we didn't dispatch any Bar
        # event.
        ret = False
        if self.__dispatchImpl(None):
            ret = True
        if super(LiveTradeFeed, self).dispatch():
            ret = True
        return ret

    # This should not raise.
    def stop(self):
        try:
            self.__stopped = True
            if self.__thread is not None and self.__thread.is_alive():
                logger.info("Stopping websocket client.")
                self.__thread.stop()
        except Exception as e:
            logger.error("Error shutting down client: %s" % (str(e)))

    # This should not raise.
    def join(self):
        if self.__thread is not None:
            self.__thread.join()

    def eof(self):
        return self.__stopped

    def getOrderBookUpdateEvent(self):
        """
        Returns the event that will be emitted when the orderbook gets updated.

        Eventh handlers should receive one parameter:
         1. A :class:`pyalgotrade.bitstamp.wsclient.OrderBookUpdate` instance.

        :rtype: :class:`pyalgotrade.observer.Event`.
        """
        return self.__orderBookUpdateEvent
