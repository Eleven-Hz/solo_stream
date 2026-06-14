"""PyInstaller 런타임 훅 — multiprocessing spawn 안전장치.

frozen + spawn 환경에서 자식 프로세스가 앱 전체를 다시 실행해 무한 창 생성으로
폭주하는 대표 함정을 막는다. freeze_support() 를 부트스트랩 최초 시점에 호출해
자식이 target 함수만 실행하고 빠져나가도록 보장한다.

main.py 의 `if __name__ == '__main__'` 안에도 freeze_support() 가 있지만,
spawn 자식은 그 블록을 실행하지 않으므로 런타임 훅으로 한 번 더 못박는다.
"""
import multiprocessing

multiprocessing.freeze_support()
