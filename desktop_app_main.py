def main():
    app = QApplication(sys.argv)
    app.setApplicationName("仙人掌 Agent")
    # 跳过图标加载，避免PySide6崩溃（PNG/ICO格式问题）
    # try:
    #     if os.path.exists(ICON_PNG):
    #         icon = QIcon(ICON_PNG)
    #         if not icon.isNull():
    #             app.setWindowIcon(icon)
    # except Exception as e:
    #     print(f"[WARN] Icon load failed: {e}")
    win = XianRenZhangWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
