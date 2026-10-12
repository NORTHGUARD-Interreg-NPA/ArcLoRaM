/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    sys_app.c
  * @author  MCD Application Team
  * @brief   Initializes HW and SW system entities (not related to the radio)
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include <stdio.h>
#include "platform.h"
#include "sys_app.h"
#include "stm32_seq.h"
#include "stm32_systime.h"
#include "stm32_lpm.h"
#include "timer_if.h"
#include "utilities_def.h"
#include "msg_id.h"
#include "mbmuxif_sys.h"
#include "mbmuxif_trace.h"
#include "mbmuxif_radio.h"

/* USER CODE BEGIN Includes */
#include "arclog.h"
#include "core_fault.h"
#include "core_state_glue.h"
#include <string.h>
/* USER CODE END Includes */

/* External variables ---------------------------------------------------------*/
/* USER CODE BEGIN EV */
extern RTC_HandleTypeDef hrtc;
extern volatile uint8_t g_stop2_flag;
/* USER CODE END EV */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* One STOP2_WAKES summary line every N CM4 wakes (~1 per second). */
#define STOP2_WAKE_REPORT_EVERY  64u

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
uint32_t InstanceIndex;
uint8_t SYS_Cm0plusRdyNotificationFlag = 0;
static uint8_t SYS_TimerInitialisedFlag = 0;

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
/**
  * @brief Initialize MBMUX, wait CM0PLUS is ready, gets CM0PLUS capabilities, Initialize other features
  */
static void MBMUXIF_Init(void);

/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Exported functions ---------------------------------------------------------*/
void SystemApp_Init(void)
{
  /* USER CODE BEGIN SystemApp_Init_1 */
UTIL_ADV_TRACE_Init();
UTIL_ADV_TRACE_RegisterTimeStampFunction(TimestampNow);

  /* Select MSI as the wakeup clock so the core restarts on MSI after STOP2 */
  __HAL_RCC_WAKEUPSTOP_CLK_CONFIG(RCC_STOP_WAKEUPCLOCK_MSI);

  /* Initialize LPM bitmask state */
  UTIL_LPM_Init();

  /* Block OFF mode — STOP2 is the deepest mode allowed */
  UTIL_LPM_SetOffMode((1 << CFG_LPM_DUMMY_Id), UTIL_LPM_DISABLE);
  /* USER CODE END SystemApp_Init_1 */

  /*Initialize MBMux (to be done after LPM because MBMux uses the sequencer) */
  MBMUXIF_Init();

  /* USER CODE BEGIN SystemApp_Init_2 */

  /* USER CODE END SystemApp_Init_2 */
}

void Process_Sys_Notif(MBMUX_ComParam_t *ComObj)
{
  /* USER CODE BEGIN Process_Sys_Notif_1 */

  /* USER CODE END Process_Sys_Notif_1 */
  uint32_t  notif_ack_id;

  notif_ack_id = ComObj->MsgId;

  switch (notif_ack_id)
  {
    case SYS_RTC_ALARM_MSG_ID:
      /* USER CODE BEGIN Process_Sys_Notif_RTC_ALARM */

      /* USER CODE END Process_Sys_Notif_RTC_ALARM */
      break;
    case SYS_OTHER_MSG_ID:
      APP_LOG(TS_ON, VLEVEL_H, "CM4<(System)\r\n");
      /* prepare ack buffer*/
      ComObj->ParamCnt = 0;
      ComObj->ReturnVal = 0; /* dummy value  */
      /* USER CODE BEGIN Process_Sys_Notif_OTHER */

      /* USER CODE END Process_Sys_Notif_OTHER */
      break;
    default:
      /* USER CODE BEGIN Process_Sys_Notif_DEFAULT */

      /* USER CODE END Process_Sys_Notif_DEFAULT */
      break;
  }

  /* Send ack*/
  APP_LOG(TS_ON, VLEVEL_H, "CM4>(System)\r\n");
  MBMUXIF_SystemSendAck(FEAT_INFO_SYSTEM_ID);
  /* USER CODE BEGIN Process_Sys_Notif_2 */

  /* USER CODE END Process_Sys_Notif_2 */
}

void UTIL_SEQ_EvtIdle(uint32_t TaskId_bm, uint32_t EvtWaited_bm)
{
  /**
    * overwrites the __weak UTIL_SEQ_EvtIdle() in stm32_seq.c
    * all to process all tack except TaskId_bm
    */
  /* USER CODE BEGIN UTIL_SEQ_EvtIdle_1 */

  /* USER CODE END UTIL_SEQ_EvtIdle_1 */
  UTIL_SEQ_Run(~TaskId_bm);
  /* USER CODE BEGIN UTIL_SEQ_EvtIdle_2 */

  /* USER CODE END UTIL_SEQ_EvtIdle_2 */
  return;
}

/* USER CODE BEGIN EF */
void UTIL_SEQ_Idle(void)
{
  UTIL_LPM_EnterLowPower();
}

void UTIL_ADV_TRACE_PreSendHook(void)
{
  UTIL_LPM_SetStopMode((1 << CFG_LPM_UART_TX_Id), UTIL_LPM_DISABLE);
}

void UTIL_ADV_TRACE_PostSendHook(void)
{
  UTIL_LPM_SetStopMode((1 << CFG_LPM_UART_TX_Id), UTIL_LPM_ENABLE);
}

void UTIL_SEQ_PostIdle(void)
{
  CoreWatch_PostIdle();
  /* CM4 wakes from STOP2 about once a second; a line per wake would eat a
   * large share of the 9600-baud trace link. Report a summary instead. */
  static uint32_t stop2_wakes;
  if (g_stop2_flag)
  {
    g_stop2_flag = 0;
    stop2_wakes++;
    if ((stop2_wakes % STOP2_WAKE_REPORT_EVERY) == 0u)
    {
      ARCLOG(ARCLOG_MOD_POWER, VLEVEL_M, "STOP2_WAKES", "n=%u", (unsigned)stop2_wakes);
    }
  }
}

void TimestampNow(uint8_t *buff, uint16_t *size)
{
  RTC_TimeTypeDef sTime = {0};
  RTC_DateTypeDef sDate = {0};
  HAL_RTC_GetTime(&hrtc, &sTime, RTC_FORMAT_BIN);
  HAL_RTC_GetDate(&hrtc, &sDate, RTC_FORMAT_BIN); /* mandatory: unlocks shadow registers */
  /* ArcLog timestamp "YYMMDDTHHMMSS.ssss " from the shared RTC (see arclog.h). */
  *size = ArcLog_FormatTimestamp(buff, sDate.Year, sDate.Month, sDate.Date,
                                 sTime.Hours, sTime.Minutes, sTime.Seconds,
                                 sTime.SubSeconds, RTC_PREDIV_S);
}
/* USER CODE END EF */

/* Private functions ---------------------------------------------------------*/
static void MBMUXIF_Init(void)
{
  /* USER CODE BEGIN MBMUXIF_Init_1 */
UTIL_ADV_TRACE_SetVerboseLevel(VLEVEL_H); //has to be set after MBMUXIF_TraceInit().

  FEAT_INFO_List_t *p_cm0plus_supported_features_list;
  int8_t init_status;

  ARCLOG(ARCLOG_MOD_SYS, VLEVEL_ALWAYS, "BOOT", "build=%s rst=%s rsf=%08x", BENCH_BUILD_ID,
         CoreFault_ResetName(), (unsigned)CoreFault_ResetFlags());
  CoreFault_ReportAtBoot();
  CoreStateBoot_Report();

  init_status = MBMUXIF_SystemInit();
  if (init_status < 0)
  {
    Error_Handler();
  }

  /* start CM0PLUS */
  /* Note: when debugging in order to connect with the debugger CPU2 shall be start using workspace CM4 starts CM0PLUS */
  /* On the other hand is up to the developer make sure the CM0PLUS debugger is run after CM4 debugger */
  HAL_PWREx_ReleaseCore(PWR_CORE_CPU2);

  /* CM4 has started and it has reset the mailbox and initialized the MbMux; */
  /* once CM0PLUS is also initialized it send a SYS notification */
  MBMUXIF_SetCpusSynchroFlag(CPUS_BOOT_SYNC_ALLOW_CPU2_TO_START);

  ARCLOG(ARCLOG_MOD_MBMUX, VLEVEL_M, "CORE_SYNC", "stage=wait_cm0");

  MBMUXIF_WaitCm0MbmuxIsInitialized();

  ARCLOG(ARCLOG_MOD_MBMUX, VLEVEL_M, "CORE_SYNC", "stage=cm0_up");

  p_cm0plus_supported_features_list = MBMUXIF_SystemSendCm0plusInfoListReq();
  MBMUX_SetCm0plusFeatureListPtr(p_cm0plus_supported_features_list);

  ARCLOG(ARCLOG_MOD_MBMUX, VLEVEL_M, "CORE_SYNC", "stage=linked");



 #if 0 //I Do this because I want Trace to be init before setting RTC_REGISTERED flag. In this way I can be sure that Trace ACK callback is registered before returning from MBMUXIF_Init() and eventually receiving Trace notifications from CM0PLUS.
  /* USER CODE END MBMUXIF_Init_1 */
  FEAT_INFO_List_t *p_cm0plus_supported_features_list;
  int8_t init_status;

  APP_LOG(TS_ON, VLEVEL_H, "\r\nCM4: System Initialization started \r\n");

  init_status = MBMUXIF_SystemInit();
  if (init_status < 0)
  {
    Error_Handler();
  }

  /* start CM0PLUS */
  /* Note: when debugging in order to connect with the debugger CPU2 shall be start using workspace CM4 starts CM0PLUS */
  /* On the other hand is up to the developer make sure the CM0PLUS debugger is run after CM4 debugger */
  HAL_PWREx_ReleaseCore(PWR_CORE_CPU2);

  /* CM4 has started and it has reset the mailbox and initialized the MbMux; */
  /* once CM0PLUS is also initialized it send a SYS notification */
  MBMUXIF_SetCpusSynchroFlag(CPUS_BOOT_SYNC_ALLOW_CPU2_TO_START);

  APP_LOG(TS_ON, VLEVEL_H, "CM4: System Initialization done: Wait for CM0PLUS \r\n");

  MBMUXIF_WaitCm0MbmuxIsInitialized();

  APP_LOG(TS_ON, VLEVEL_H, "CM0PLUS: System Initialization started \r\n");

  p_cm0plus_supported_features_list = MBMUXIF_SystemSendCm0plusInfoListReq();
  MBMUX_SetCm0plusFeatureListPtr(p_cm0plus_supported_features_list);

  APP_LOG(TS_ON, VLEVEL_H, "System Initialization CM4-CM0PLUS completed \r\n");

  MBMUXIF_SetCpusSynchroFlag(CPUS_BOOT_SYNC_RTC_REGISTERED);
  APP_LOG(TS_ON, VLEVEL_H, "System_Priority_A Registration for RTC Alarm handling completed \r\n");

  /* USER CODE BEGIN MBMUXIF_Init_Last */

  #endif
MBMUXIF_TraceInit();
  MBMUXIF_SetCpusSynchroFlag(CPUS_BOOT_SYNC_RTC_REGISTERED);
  ARCLOG(ARCLOG_MOD_MBMUX, VLEVEL_M, "CORE_SYNC", "stage=rtc_registered");

UTIL_TIMER_Init();
SYS_TimerInitialisedFlag = 1;
  /* USER CODE END MBMUXIF_Init_Last */
}
/* USER CODE BEGIN PrFD */

/* USER CODE END PrFD */

/* HAL overload functions ---------------------------------------------------------*/

/* Set #if 0 if you want to keep the default HAL instead overcharge them*/
/* USER CODE BEGIN Overload_HAL_weaks_1 */
#if 1
/* USER CODE END Overload_HAL_weaks_1 */

/* USER CODE BEGIN Overload_HAL_weaks_1a */

/* USER CODE END Overload_HAL_weaks_1a */

/**
  * @note This function overwrites the __weak one from HAL
  */
uint32_t HAL_GetTick(void)
{
  uint32_t ret = 0;
  /* TIMER_IF can be based on other counter the SysTick e.g. RTC */
  /* USER CODE BEGIN HAL_GetTick_1 */

  /* USER CODE END HAL_GetTick_1 */
  if (SYS_TimerInitialisedFlag == 0)
  {
    /* TIMER_IF_GetTimerValue should be used only once UTIL_TIMER_Init() is initialized */
    /* If HAL_Delay or a TIMEOUT countdown is necessary during initialization phase */
    /* please use temporarily another timebase source (SysTick or TIMx), which implies also */
    /* to rework the above function HAL_InitTick() and to call HAL_IncTick() on the timebase IRQ */
    /* Note: In DualCore UTIL_TIMER_Init() is called rather late because it needs first CM0PLUS to init RTC */
    /* USER CODE BEGIN HAL_GetTick_EarlyCall */

    /* USER CODE END HAL_GetTick_EarlyCall */
  }
  else
  {
    ret = TIMER_IF_GetTimerValue();
  }
  /* USER CODE BEGIN HAL_GetTick_2 */

  /* USER CODE END HAL_GetTick_2 */
  return ret;
}

/**
  * @note This function overwrites the __weak one from HAL
  */
void HAL_Delay(__IO uint32_t Delay)
{
  /* TIMER_IF can be based on other counter the SysTick e.g. RTC */
  /* USER CODE BEGIN HAL_Delay_1 */

  /* USER CODE END HAL_Delay_1 */
  TIMER_IF_DelayMs(Delay);
  /* USER CODE BEGIN HAL_Delay_2 */

  /* USER CODE END HAL_Delay_2 */
}

/* USER CODE BEGIN Overload_HAL_weaks_2 */
#endif /* 1 default HAL overcharge */
/* if needed set #if 0 and redefine here your own "Tick" functions*/

/* USER CODE END Overload_HAL_weaks_2 */
