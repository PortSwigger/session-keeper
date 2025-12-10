# -*- coding: utf-8 -*-
from burp import IBurpExtender, ITab, IContextMenuFactory, IExtensionStateListener
from java.util import ArrayList, Date
from javax.swing import (
    JPanel, JButton, JTextField, JTextArea, JLabel,
    JScrollPane, JMenuItem, BoxLayout, JTabbedPane, JSplitPane
)
from java.awt import BorderLayout
from java.lang import Runnable, Thread
from java.awt.event import MouseAdapter, MouseEvent, FocusAdapter
from javax.swing.event import DocumentListener
from java.text import SimpleDateFormat
import time


class KeepAliveSender(Runnable):
    def __init__(self, panel, interval, max_requests):
        self.panel = panel
        self.interval = interval
        self.max_requests = max_requests  # -1 = unlimited
        self.running = True
        self.sent_count = 0

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            self.panel.update_countdown(self.interval)
            for i in range(self.interval, 0, -1):
                if not self.running:
                    return
                self.panel.update_countdown(i)
                time.sleep(1)

            if not self.running:
                return

            try:
                request = self.panel.target_request.getRequest()
                service = self.panel.target_http_service

                response_obj = self.panel.callbacks.makeHttpRequest(service, request)
                resp_data = response_obj.getResponse()

                if resp_data:
                    resp_info = self.panel.helpers.analyzeResponse(resp_data)
                    status = resp_info.getStatusCode()
                    status_line = resp_info.getHeaders()[0]
                    self.panel.set_status("Last: %d %s (Sent: %d)" % (
                        status, status_line, self.sent_count + 1))
                    self.panel.response_area.setText(
                        self.panel.helpers.bytesToString(resp_data)
                    )
                    self.panel.log_status("%d %s" % (status, status_line))
                else:
                    self.panel.set_status("No response (Sent: %d)" % (self.sent_count + 1))
                    self.panel.log_status("ERROR: No response")

                self.sent_count += 1
                if self.max_requests != -1 and self.sent_count >= self.max_requests:
                    self.panel.set_status("Stopped after %d requests" % self.sent_count)
                    self.panel.stop_sender()
                    return
            except Exception as e:
                self.panel.set_status("Error: %s" % str(e))
                self.panel.log_status("ERROR: %s" % str(e))
                return


class SessionPanel(JPanel):
    def __init__(self, callbacks, helpers, name, extender):
        JPanel.__init__(self, BorderLayout())
        self.callbacks = callbacks
        self.helpers = helpers
        self.name = name
        self.extender = extender

        self.target_request = None
        self.target_http_service = None
        self.sender = None
        self.sender_thread = None

        self.build_ui()

    def build_ui(self):
        control_panel = JPanel()
        self.interval_field = JTextField("10", 5)
        self.max_requests_field = JTextField("", 5)
        self.start_button = JButton("Start", actionPerformed=self.start)
        self.stop_button = JButton("Stop", actionPerformed=self.stop)
        clear_log_button = JButton("Clear Log", actionPerformed=lambda e: self.log_area.setText(""))

        self.countdown_label = JLabel("Countdown: -")
        self.status_label = JLabel("Last: -")
        self.stop_button.setEnabled(False)

        control_panel.add(JLabel("Interval (s):"))
        control_panel.add(self.interval_field)
        control_panel.add(JLabel("Max Requests:"))
        control_panel.add(self.max_requests_field)
        control_panel.add(self.start_button)
        control_panel.add(self.stop_button)
        control_panel.add(clear_log_button)
        control_panel.add(self.countdown_label)
        control_panel.add(self.status_label)
        self.add(control_panel, BorderLayout.NORTH)

        self.request_area = JTextArea()
        self.request_area.setEditable(False)
        self.request_area.setLineWrap(True)
        self.request_area.setWrapStyleWord(True)
        request_scroll = JScrollPane(self.request_area)

        self.response_area = JTextArea()
        self.response_area.setEditable(False)
        self.response_area.setLineWrap(True)
        self.response_area.setWrapStyleWord(True)
        response_scroll = JScrollPane(self.response_area)

        self.log_area = JTextArea()
        self.log_area.setEditable(False)
        self.log_area.setLineWrap(True)
        self.log_area.setWrapStyleWord(True)
        log_scroll = JScrollPane(self.log_area)

        lower_split = JSplitPane(JSplitPane.VERTICAL_SPLIT, response_scroll, log_scroll)
        lower_split.setResizeWeight(0.818)  # 45/(45+10)
        lower_split.setDividerLocation(0.818)

        full_split = JSplitPane(JSplitPane.VERTICAL_SPLIT, request_scroll, lower_split)
        full_split.setResizeWeight(0.5)  # 45/(45+55)
        full_split.setDividerLocation(0.45)

        self.add(full_split, BorderLayout.CENTER)

        class FieldChangeListener(DocumentListener):
            def __init__(slf, panel):
                slf.panel = panel

            def insertUpdate(slf, e):
                slf.panel.stop_sender()

            def removeUpdate(slf, e):
                slf.panel.stop_sender()

            def changedUpdate(slf, e):
                slf.panel.stop_sender()

        listener = FieldChangeListener(self)
        self.interval_field.getDocument().addDocumentListener(listener)
        self.max_requests_field.getDocument().addDocumentListener(listener)

    def load_request(self, request_response):
        self.target_request = request_response
        svc = request_response.getHttpService()
        self.target_http_service = self.helpers.buildHttpService(
            svc.getHost(), svc.getPort(), svc.getProtocol()
        )
        self.request_area.setText(self.helpers.bytesToString(request_response.getRequest()))
        self.response_area.setText("")
        self.log_area.setText("")
        self.set_status("Request loaded.")

    def log_status(self, status_line):
        now = SimpleDateFormat("HH:mm:ss").format(Date())
        self.log_area.append("[%s] %s\n" % (now, status_line))
        self.log_area.setCaretPosition(self.log_area.getDocument().getLength())

    def start(self, event):
        if not self.target_request:
            self.set_status("No request loaded")
            return

        try:
            interval = int(self.interval_field.getText())
        except:
            self.set_status("Invalid interval")
            return

        max_req_txt = self.max_requests_field.getText().strip()
        try:
            max_requests = int(max_req_txt) if max_req_txt else -1
        except:
            self.set_status("Invalid max requests")
            return

        self.stop_sender()
        self.sender = KeepAliveSender(self, interval, max_requests)
        self.sender_thread = Thread(self.sender)
        self.sender_thread.start()
        self.extender.update_tab_status(self, True)
        self.set_status("Started")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop(self, event):
        self.stop_sender()
        self.set_status("Stopped manually")

    def stop_sender(self):
        if self.sender:
            self.sender.stop()
            self.sender = None
        if self.sender_thread:
            self.sender_thread = None
        self.extender.update_tab_status(self, False)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def update_countdown(self, val):
        self.countdown_label.setText("Countdown: %ss" % val)

    def set_status(self, msg):
        self.status_label.setText(msg)


class BurpExtender(IBurpExtender, ITab, IContextMenuFactory, IExtensionStateListener):
    def registerExtenderCallbacks(self, callbacks):
        self.callbacks = callbacks
        self.helpers = callbacks.getHelpers()
        callbacks.setExtensionName("Session Keeper")
        callbacks.registerContextMenuFactory(self)
        callbacks.registerExtensionStateListener(self)

        self.session_tabs = JTabbedPane()
        self.session_tab_names = {}  # {SessionPanel: "base name"}
        self.session_tabs.addMouseListener(self.TabMouseListener(self))
        self.session_count = 0
        self.main_panel = JPanel(BorderLayout())
        self.main_panel.add(self.session_tabs, BorderLayout.CENTER)
        self.add_new_session()
        callbacks.addSuiteTab(self)

    def getTabCaption(self):
        return "Session Keeper"

    def getUiComponent(self):
        return self.main_panel

    def createMenuItems(self, invocation):
        menu = ArrayList()
        item = JMenuItem("Send to Session Keeper",
                         actionPerformed=lambda e: self.send_to_new_session(invocation))
        menu.add(item)
        return menu

    def send_to_new_session(self, invocation):
        selected = invocation.getSelectedMessages()
        if selected and len(selected) > 0:
            tab = self.add_new_session()
            tab.load_request(selected[0])
            self.session_tabs.setSelectedComponent(tab)

    def add_new_session(self):
        self.session_count += 1
        name = "Session %d" % self.session_count
        tab = SessionPanel(self.callbacks, self.helpers, name, self)
        self.session_tabs.addTab(name + " [STOP]", tab)
        self.session_tab_names[tab] = name
        return tab

    def update_tab_status(self, panel, running):
        base_name = self.session_tab_names.get(panel, "Session")
        icon = "[RUN]" if running else "[STOP]"
        idx = self.session_tabs.indexOfComponent(panel)
        if idx != -1:
            self.session_tabs.setTitleAt(idx, base_name + " " + icon)

    def extensionUnloaded(self):
        """
        Called by Burp when the extension is unloaded.
        Stop any background sender threads cleanly.
        """
        try:
            tab_count = self.session_tabs.getTabCount()
        except Exception:
            return

        for i in range(tab_count):
            comp = self.session_tabs.getComponentAt(i)
            if isinstance(comp, SessionPanel):
                comp.stop_sender()

    class TabMouseListener(MouseAdapter):
        def __init__(self, extender):
            self.extender = extender

        def mouseClicked(self, e):
            if e.getClickCount() == 2:
                tabbed = self.extender.session_tabs
                index = tabbed.getUI().tabForCoordinate(tabbed, e.getX(), e.getY())
                if index != -1:
                    component = tabbed.getComponentAt(index)
                    current_title = tabbed.getTitleAt(index).rsplit(" ", 1)[0]
                    editor = JTextField(current_title)
                    editor.selectAll()

                    def apply_name(_):
                        new_name = editor.getText().strip()
                        if new_name:
                            self.extender.session_tab_names[component] = new_name
                            status = "[RUN]" if component.sender else "[STOP]"
                            tabbed.setTitleAt(index, new_name + " " + status)
                        tabbed.setTabComponentAt(index, None)

                    editor.addActionListener(apply_name)

                    class FocusHandler(FocusAdapter):
                        def focusLost(self, _):
                            apply_name(None)

                    editor.addFocusListener(FocusHandler())
                    tabbed.setTabComponentAt(index, editor)
                    editor.requestFocusInWindow()
