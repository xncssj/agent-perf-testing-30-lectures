<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { changePassword, updateProfile } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const saving = ref(false)
const changing = ref(false)
const profile = reactive({
  display_name: '',
  email: '',
  phone: '',
  title: '',
})
const pwd = reactive({ old_password: '', new_password: '', again: '' })

onMounted(() => {
  const u = auth.user
  if (!u) return
  profile.display_name = u.display_name
  profile.email = u.email
  profile.phone = u.phone
  profile.title = u.title
})

async function saveProfile() {
  saving.value = true
  try {
    const data = await updateProfile(profile)
    auth.applyUser(data.user)
    ElMessage.success('个人信息已更新')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function savePassword() {
  if (pwd.new_password !== pwd.again) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  changing.value = true
  try {
    await changePassword(pwd.old_password, pwd.new_password)
    pwd.old_password = ''
    pwd.new_password = ''
    pwd.again = ''
    ElMessage.success('密码已更新，下次登录请使用新密码')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '修改失败')
  } finally {
    changing.value = false
  }
}
</script>

<template>
  <div class="profile">
    <h2>个人信息</h2>
    <p class="text-sm">账号 {{ auth.user?.username }} · 密码以 PBKDF2 摘要存储，页面不回显原密码。</p>
    <el-card shadow="never">
      <el-form label-width="88px">
        <el-form-item label="显示名">
          <el-input v-model="profile.display_name" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="profile.email" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="profile.phone" />
        </el-form-item>
        <el-form-item label="职务">
          <el-input v-model="profile.title" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveProfile">保存资料</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <h2>修改密码</h2>
    <el-card shadow="never">
      <el-form label-width="88px">
        <el-form-item label="原密码">
          <el-input v-model="pwd.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwd.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认">
          <el-input v-model="pwd.again" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="changing" @click="savePassword">更新密码</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.profile {
  height: 100%;
  overflow-y: auto;
  padding: 16px 20px;
  max-width: 640px;
}
h2 {
  font-size: 16px;
  margin: 0 0 8px;
}
.el-card {
  margin-bottom: 20px;
}
</style>
