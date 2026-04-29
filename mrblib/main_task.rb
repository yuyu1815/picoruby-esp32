require 'machine'
def ai_boot_mark(stage)
  puts "AI_BOOT stage=#{stage} uptime_us=#{Machine.uptime_us}"
  if Machine.respond_to?(:memory_snapshot)
    puts "AI_MEM stage=#{stage} #{Machine.memory_snapshot}"
  end
  if Machine.respond_to?(:cpu_snapshot)
    puts "AI_CPU stage=#{stage} #{Machine.cpu_snapshot}"
  end
rescue => e
  puts "AI_BOOT_ERROR stage=#{stage} #{e.message} (#{e.class})"
end

ai_boot_mark("main_task_start")
require "watchdog"
Watchdog.disable
ai_boot_mark("watchdog_disabled")
require "shell"
ai_boot_mark("shell_required")
STDIN = IO.new
STDOUT = IO.new

# Setup flash disk
begin
  STDIN.echo = false
  puts "Initializing FLASH disk as the root volume... "
  Shell.setup_root_volume(:flash, label: 'storage')
  Shell.setup_system_files
  puts "Available"
  ai_boot_mark("flash_disk_done")
rescue => e
  puts "Not available"
  puts "#{e.message} (#{e.class})"
  ai_boot_mark("flash_disk_failed")
end

begin
  if Machine.wifi_available?
    ARGV[0] = "--check-auto-connect"
    load "/bin/wifi_connect"
    ARGV.clear
    ai_boot_mark("wifi_auto_connect_done")
  end

  GC.start
  ai_boot_mark("gc_after_wifi")

  if File.exist?("/home/app.mrb")
    puts "Loading app.mrb"
    ai_boot_mark("app_mrb_load_start")
    load "/home/app.mrb"
    ai_boot_mark("app_mrb_load_done")
  elsif File.exist?("/home/app.rb")
    puts "Loading app.rb"
    ai_boot_mark("app_rb_load_start")
    load "/home/app.rb"
    ai_boot_mark("app_rb_load_done")
  end

  GC.start
  ai_boot_mark("gc_before_shell")

  $shell = Shell.new(clean: true)
  puts "Starting shell...\n\n"
  ai_boot_mark("shell_created")

  $shell.show_logo
  ai_boot_mark("shell_logo_done")
  $shell.start
rescue => e
  puts "#{e.message} (#{e.class})"
  ai_boot_mark("main_task_rescue")
end
